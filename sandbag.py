#!/usr/bin/env python3
"""Sandbag: offline sandbag-wall and flood-barrier planner.

Reads one plain-text plan file and works out how many sandbags a wall needs,
how much sand they take, what to order, what it costs, how long it takes to
build, and whether the finished wall clears the flood depth plus freeboard.

Standard library only. Run ``python3 sandbag.py --help`` for the commands.
"""

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from pathlib import Path

CENT = Decimal("0.01")
REQUIRED = object()

_HEADER_RE = re.compile(r"^\[(\w+)(?:\s+(.+?))?\]$")
_LENGTH_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|cm|mm|ft|in)$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_INT_RE = re.compile(r"^\d+$")
_MONEY_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")
_DENSITY_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:kg/m3|kg/m³)?$", re.IGNORECASE)
_UNITS = {
    "m": Decimal("1"),
    "cm": Decimal("0.01"),
    "mm": Decimal("0.001"),
    "ft": Decimal("0.3048"),
    "in": Decimal("0.0254"),
}


@dataclass(frozen=True)
class Issue:
    line: int
    message: str


@dataclass
class Plan:
    bag: dict
    sand: dict
    site: dict
    walls: list


@dataclass
class WallResult:
    name: str
    net_length: Decimal
    bays: int  # bags along the wall in one full course
    across: int  # bags across the wall thickness
    courses: int
    offset_courses: int
    bags: int
    built_height: Decimal


# ---------------------------------------------------------------- value parsers

def parse_length(text, allow_zero=False):
    """Parse '0.6 m', '60cm', '2 ft' and similar into metres."""
    match = _LENGTH_RE.match(text.strip())
    if not match:
        raise ValueError(f"'{text}' is not a length; use a number with m, cm, mm, ft or in")
    value = Decimal(match.group(1)) * _UNITS[match.group(2).lower()]
    if value == 0 and not allow_zero:
        raise ValueError("must be greater than zero")
    return value


def parse_length_list(text):
    """Parse a comma-separated list of lengths; 'none' or '-' means no items."""
    if text.strip().lower() in ("none", "-"):
        return []
    return [parse_length(part) for part in text.split(",")]


def parse_number(text, allow_zero=False):
    if not _NUMBER_RE.match(text.strip()):
        raise ValueError(f"'{text}' is not a positive number")
    value = Decimal(text.strip())
    if value == 0 and not allow_zero:
        raise ValueError("must be greater than zero")
    return value


def parse_int(text):
    if not _INT_RE.match(text.strip()) or int(text) < 1:
        raise ValueError(f"'{text}' is not a whole number of at least 1")
    return int(text)


def parse_percent(text, allow_zero=False):
    """Parse '75%' or '75' into a fraction (0.75)."""
    raw = text.strip().rstrip("%").strip()
    if not _NUMBER_RE.match(raw):
        raise ValueError(f"'{text}' is not a percentage")
    value = Decimal(raw) / 100
    if value > 1:
        raise ValueError("cannot be more than 100%")
    if value == 0 and not allow_zero:
        raise ValueError("must be greater than zero")
    return value


def parse_money(text):
    if not _MONEY_RE.match(text.strip()):
        raise ValueError(f"'{text}' is not an amount; use digits with at most two decimals")
    return Decimal(text.strip())


def parse_density(text):
    match = _DENSITY_RE.match(text.strip())
    if not match or Decimal(match.group(1)) == 0:
        raise ValueError(f"'{text}' is not a density; use kg/m3, e.g. 1600 kg/m3")
    return Decimal(match.group(1))


def parse_bool(text):
    value = text.strip().lower()
    if value in ("yes", "true", "on"):
        return True
    if value in ("no", "false", "off"):
        return False
    raise ValueError(f"'{text}' is not yes or no")


# ------------------------------------------------------------------ schemas

SCHEMAS = {
    "bags": {
        "length": (parse_length, REQUIRED),
        "width": (parse_length, REQUIRED),
        "height": (parse_length, REQUIRED),
        "fill": (parse_percent, Decimal("0.75")),
        "per_pack": (parse_int, 1),
        "pack_price": (parse_money, Decimal("0")),
    },
    "sand": {
        "density": (parse_density, Decimal("1600")),
        "price_per_tonne": (parse_money, Decimal("0")),
        "delivery": (parse_money, Decimal("0")),
    },
    "site": {
        "flood_depth": (parse_length, None),
        "freeboard": (partial(parse_length, allow_zero=True), Decimal("0.30")),
        "stagger": (parse_bool, True),
        "spare": (partial(parse_percent, allow_zero=True), Decimal("0.05")),
        "crew": (parse_int, 1),
        "bags_per_hour": (parse_number, Decimal("20")),
    },
    "wall": {
        "length": (parse_length, REQUIRED),
        "thickness": (parse_length, REQUIRED),
        "height": (parse_length, REQUIRED),
        "openings": (parse_length_list, []),
    },
}


def _build(kind, label, header_line, entries, issues):
    """Turn one section's raw strings into typed values, collecting issues."""
    schema = SCHEMAS[kind]
    values = {}
    for key, (convert, default) in schema.items():
        if key in entries:
            raw, line = entries[key]
            try:
                values[key] = convert(raw)
            except ValueError as exc:
                issues.append(Issue(line, f"{key}: {exc}"))
        elif default is REQUIRED:
            issues.append(Issue(header_line, f"[{label}] is missing required key '{key}'"))
        else:
            values[key] = default
    for key, (_, line) in entries.items():
        if key not in schema:
            issues.append(Issue(line, f"unknown key '{key}' in [{label}]"))
    return values


def parse_plan(text):
    """Parse plan text. Returns (plan, issues); plan is None when issues exist."""
    issues = []
    found = []  # (kind, name, header_line, entries)
    current = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = _HEADER_RE.match(line)
        if header:
            kind = header.group(1).lower()
            name = (header.group(2) or "").strip()
            if kind not in SCHEMAS:
                issues.append(Issue(lineno, f"unknown section [{header.group(1)}]"))
                kind = "unknown"
            current = (kind, name, lineno, {})
            found.append(current)
            continue
        if current is None:
            issues.append(Issue(lineno, "value outside any [section]"))
            continue
        if "=" not in line:
            issues.append(Issue(lineno, "expected 'key = value' or a [section] header"))
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        entries = current[3]
        if key in entries:
            issues.append(Issue(lineno, f"duplicate key '{key}' (first set on line {entries[key][1]})"))
        elif not value:
            issues.append(Issue(lineno, f"'{key}' has no value"))
        else:
            entries[key] = (value, lineno)

    bag = sand = site = None
    walls = []
    seen_singletons = {}
    seen_walls = {}
    for kind, name, lineno, entries in found:
        if kind == "unknown":
            continue
        if kind in ("bags", "sand", "site"):
            if kind in seen_singletons:
                issues.append(Issue(lineno, f"duplicate [{kind}] section (first on line {seen_singletons[kind]})"))
                continue
            seen_singletons[kind] = lineno
            values = _build(kind, kind, lineno, entries, issues)
            if kind == "bags":
                bag = values
            elif kind == "sand":
                sand = values
            else:
                site = values
            continue
        name = name or f"wall {len(walls) + 1}"
        if name.lower() in seen_walls:
            issues.append(Issue(lineno, f"duplicate wall name '{name}' (first on line {seen_walls[name.lower()]})"))
            continue
        seen_walls[name.lower()] = lineno
        values = _build("wall", f"wall {name}", lineno, entries, issues)
        values.update(name=name, line=lineno)
        walls.append(values)

    if bag is None and "bags" not in seen_singletons:
        issues.append(Issue(1, "missing [bags] section"))
    if not walls:
        issues.append(Issue(1, "need at least one [wall NAME] section"))
    for wall in walls:
        if "length" in wall and "openings" in wall:
            for opening in wall["openings"]:
                if opening >= wall["length"]:
                    issues.append(Issue(wall["line"], f"opening {opening} m is not shorter than wall length"))
            if sum(wall["openings"], Decimal(0)) >= wall["length"]:
                issues.append(Issue(wall["line"], "openings leave no wall length"))

    if issues:
        return None, sorted(issues, key=lambda i: i.line)
    if sand is None:
        sand = _build("sand", "sand", 0, {}, issues)
    if site is None:
        site = _build("site", "site", 0, {}, issues)
    return Plan(bag=bag, sand=sand, site=site, walls=walls), []


def lint_text(text):
    """Return the Issue list for plan text (empty when the plan is clean)."""
    _, issues = parse_plan(text)
    return issues


# ---------------------------------------------------------------- calculations

def wall_result(wall, bag, stagger):
    """Bags, courses and height for one wall. Staggered courses cost one extra bag per across-unit."""
    net = wall["length"] - sum(wall["openings"], Decimal(0))
    bays = math.ceil(net / bag["length"])
    across = math.ceil(wall["thickness"] / bag["width"])
    courses = math.ceil(wall["height"] / bag["height"])
    offset = courses // 2 if stagger else 0
    full = courses - offset
    bags = full * bays * across + offset * (bays + 1) * across
    return WallResult(
        name=wall["name"],
        net_length=net,
        bays=bays,
        across=across,
        courses=courses,
        offset_courses=offset,
        bags=bags,
        built_height=bag["height"] * courses,
    )


def bag_sand_m3(bag):
    """Sand volume in one filled bag, in cubic metres."""
    return bag["length"] * bag["width"] * bag["height"] * bag["fill"]


def summarise(plan):
    bag, sand, site = plan.bag, plan.sand, plan.site
    walls = [wall_result(w, bag, site["stagger"]) for w in plan.walls]
    needed = sum(w.bags for w in walls)
    spare = math.ceil(Decimal(needed) * site["spare"])
    order = needed + spare
    packs = -(-order // bag["per_pack"])
    bag_cost = (packs * bag["pack_price"]).quantize(CENT, ROUND_HALF_UP)
    sand_m3 = needed * bag_sand_m3(bag)
    tonnes = sand_m3 * sand["density"] / Decimal(1000)
    sand_cost = (tonnes * sand["price_per_tonne"]).quantize(CENT, ROUND_HALF_UP)
    total_cost = bag_cost + sand_cost + sand["delivery"]
    crew_hours = (Decimal(needed) / site["bags_per_hour"] / site["crew"]).quantize(CENT, ROUND_HALF_UP)

    warnings = []
    if site["flood_depth"] is not None:
        required = site["flood_depth"] + site["freeboard"]
        for wall in walls:
            if wall.built_height < required:
                warnings.append(
                    f"{wall.name}: built height {fmt_len(wall.built_height)} is below "
                    f"flood depth plus freeboard {fmt_len(required)}"
                )

    return {
        "walls": walls,
        "bags_needed": needed,
        "spare_bags": spare,
        "bags_to_order": order,
        "packs": packs,
        "sand_m3": sand_m3.quantize(Decimal("0.001"), ROUND_HALF_UP),
        "sand_tonnes": tonnes.quantize(Decimal("0.001"), ROUND_HALF_UP),
        "bag_cost": bag_cost,
        "sand_cost": sand_cost,
        "delivery": sand["delivery"],
        "total_cost": total_cost.quantize(CENT, ROUND_HALF_UP),
        "crew_hours": crew_hours,
        "warnings": warnings,
    }


def diagram(wall, bag, stagger, max_courses=12):
    """ASCII course diagram. '~' marks half-bags cut for the staggered ends."""
    cell = 8
    result = wall_result(wall, bag, stagger)
    shown = min(result.courses, max_courses)
    lines = [f"{wall['name']}: {result.bays} bag(s) long, {result.across} across, "
             f"{result.courses} course(s), {result.bags} bag(s)"]
    half = "~" * (cell // 2)
    rows = []
    for course in range(shown):
        offset = stagger and course % 2 == 1
        cells = "".join("[" + "bag".center(cell - 2) + "]" for _ in range(result.bays))
        if offset:
            rows.append((course, half + cells + half))
        else:
            rows.append((course, cells))
    for course, row in reversed(rows):
        label = f"course {course + 1:>2} "
        lines.append(label + row)
    if shown < result.courses:
        lines.append(f"... {result.courses - shown} more course(s) not shown")
    lines.append("~ = half-bag cut for the staggered end; offset courses start on a half bag")
    return "\n".join(lines)


# -------------------------------------------------------------------- output

def fmt_len(value):
    return f"{value:.2f} m"


def _money(value):
    return f"{value:.2f}"


def render_report(plan, summary):
    lines = [
        "SANDBAG WALL PLAN",
        "=" * 60,
        f"Bag: {fmt_len(plan.bag['length'])} x {fmt_len(plan.bag['width'])} x "
        f"{fmt_len(plan.bag['height'])}, fill {plan.bag['fill'] * 100:.0f}%",
        f"Sand: {plan.sand['density']} kg/m3, {plan.bag['per_pack']} bags per pack",
        "",
        f"{'Wall':<16}{'Length':>10}{'Courses':>9}{'Stagger':>9}{'Height':>10}{'Bags':>8}",
        "-" * 62,
    ]
    for wall in summary["walls"]:
        lines.append(
            f"{wall.name[:15]:<16}{fmt_len(wall.net_length):>10}{wall.courses:>9}"
            f"{wall.offset_courses:>9}{fmt_len(wall.built_height):>10}{wall.bags:>8}"
        )
    lines += [
        "-" * 62,
        f"Bags needed:      {summary['bags_needed']}",
        f"Spare bags:       {summary['spare_bags']} ({plan.site['spare'] * 100:.0f}%)",
        f"Bags to order:    {summary['bags_to_order']} in {summary['packs']} pack(s)",
        f"Sand:             {summary['sand_m3']} m3 = {summary['sand_tonnes']} t",
        f"Crew hours:       {summary['crew_hours']} ({plan.site['crew']} person(s))",
        "",
        f"Bag cost:         {_money(summary['bag_cost'])}",
        f"Sand cost:        {_money(summary['sand_cost'])}",
        f"Delivery:         {_money(summary['delivery'])}",
        f"Total:            {_money(summary['total_cost'])}",
    ]
    if plan.site["flood_depth"] is not None:
        lines.append("")
        lines.append(f"Flood depth {fmt_len(plan.site['flood_depth'])}, freeboard "
                     f"{fmt_len(plan.site['freeboard'])}")
    for warning in summary["warnings"]:
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines)


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, WallResult):
        return {k: _jsonable(v) for k, v in value.__dict__.items()}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


# ----------------------------------------------------------------------- CLI

def _load(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"sandbag: cannot read {path}: {exc}", file=sys.stderr)
        return None
    plan, issues = parse_plan(text)
    if issues:
        for issue in issues:
            print(f"{path}:{issue.line}: {issue.message}", file=sys.stderr)
        return None
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sandbag",
        description="Plan sandbag walls offline from one plain-text plan file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="print bags, sand, cost and flood checks")
    p_report.add_argument("plan")
    p_report.add_argument("--json", action="store_true", help="print machine-readable JSON")

    p_lint = sub.add_parser("lint", help="check a plan file and report line-numbered errors")
    p_lint.add_argument("plan")

    p_diagram = sub.add_parser("diagram", help="draw the course-by-course layout of each wall")
    p_diagram.add_argument("plan")
    p_diagram.add_argument("--wall", help="only draw the wall with this name")
    p_diagram.add_argument("--courses", type=int, default=12, help="maximum courses to draw")

    p_check = sub.add_parser("check", help="gate a plan on bag and cost limits (CI)")
    p_check.add_argument("plan")
    p_check.add_argument("--max-bags", type=int, help="fail if bags to order exceed this")
    p_check.add_argument("--max-cost", type=str, help="fail if total cost exceeds this amount")

    args = parser.parse_args(argv)

    if args.command == "lint":
        try:
            text = Path(args.plan).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"sandbag: cannot read {args.plan}: {exc}", file=sys.stderr)
            return 2
        issues = lint_text(text)
        if issues:
            for issue in issues:
                print(f"{args.plan}:{issue.line}: {issue.message}")
            return 2
        print(f"{args.plan}: OK")
        return 0

    plan = _load(args.plan)
    if plan is None:
        return 2
    summary = summarise(plan)

    if args.command == "report":
        if args.json:
            print(json.dumps(_jsonable(summary), indent=2))
        else:
            print(render_report(plan, summary))
        return 0

    if args.command == "diagram":
        walls = [w for w in plan.walls if args.wall is None or w["name"] == args.wall]
        if not walls:
            print(f"sandbag: no wall named '{args.wall}'", file=sys.stderr)
            return 2
        for wall in walls:
            print(diagram(wall, plan.bag, plan.site["stagger"], args.courses))
            print()
        return 0

    failures = []
    if args.max_bags is not None and summary["bags_to_order"] > args.max_bags:
        failures.append(f"bags to order {summary['bags_to_order']} exceed limit {args.max_bags}")
    if args.max_cost is not None:
        try:
            limit = parse_money(args.max_cost)
        except ValueError as exc:
            print(f"sandbag: --max-cost {exc}", file=sys.stderr)
            return 2
        if summary["total_cost"] > limit:
            failures.append(f"total cost {_money(summary['total_cost'])} exceeds limit {_money(limit)}")
    for warning in summary["warnings"]:
        failures.append(warning)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS: all checks within limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
