# Sandbag

**Plan a sandbag wall before the water arrives.** Sandbag reads one plain-text plan
and works out how many bags to lay, how much sand to order, what it costs, how
long a crew needs, and whether the finished wall clears your flood depth plus
freeboard.

It runs fully offline on Python 3 standard library only. No accounts, no API
keys, no network calls.

## Features

- **Bag counts per wall** from length, thickness and height, with courses worked
  out for you (a 0.15 m bag course means a 0.60 m wall needs four courses).
- **Running-bond stagger**: offset courses cost one extra cut bag per
  across-unit for the ends. Switch it off with `stagger = no`.
- **Wall thickness** adds bags across the wall, so a 0.8 m wall built from 0.4 m
  bags needs two rows of bags side by side.
- **Openings** such as doorways and gates are deducted from wall length.
- **Sand volume and weight** from bag size and fill percentage, converted to
  tonnes with your sand density.
- **Buy list**: spare-bag allowance, then rounding up to whole packs.
- **Exact cost** in cents: bags, sand by the tonne and delivery, rounded half-up.
- **Crew hours** from bags per hour and crew size.
- **Flood check**: warns for any wall whose built height is below flood depth plus freeboard.
- **Course diagrams** in ASCII, showing staggered courses and cut half-bags.
- **Line-numbered lint** for every plan error: unknown keys, bad units, duplicate
  keys or sections, missing required values and impossible openings.
- **Units**: metres, centimetres, millimetres, feet and inches.
- **Text or JSON reports** for scripts and spreadsheets.
- **CI gate**: `check` fails on bag count, cost or flood-clearance problems, with
  exit codes 0, 1 and 2.

## Quick start

Requires Python 3.9 or newer. Nothing to install for the tool itself.

```bash
python3 sandbag.py report examples/garage.txt
```

The example plan in [`examples/garage.txt`](examples/garage.txt) describes a
garage-door barrier with two walls, a doorway opening and a 0.45 m flood depth.
For that plan, the report shows 36 bags needed, 38 to order (two spares) in one
pack, 0.972 m³ of sand, and a total of 119.00. Both walls also trigger freeboard
warnings, because 0.60 m and 0.45 m are below the 0.75 m required.

To see the layout:

```bash
python3 sandbag.py diagram examples/garage.txt --wall threshold
```

### Writing your own plan

```text
# comments start with a hash
[bags]
length = 0.60 m
width = 0.40 m
height = 0.15 m
fill = 75%
per_pack = 50
pack_price = 14.90

[sand]
density = 1600 kg/m3
price_per_tonne = 38.00
delivery = 45.00

[site]
flood_depth = 0.45 m
freeboard = 0.30 m
stagger = yes
spare = 5%
crew = 2
bags_per_hour = 18

[wall threshold]
length = 3.2 m
thickness = 0.40 m
height = 0.60 m
openings = none
```

Every `[wall NAME]` section is one wall. `[bags]` is required. `[sand]` and
`[site]` are optional and fall back to the defaults shown in the table below.

| Section | Key | Default | Meaning |
|---|---|---|---|
| bags | `length`, `width`, `height` | required | Filled bag dimensions |
| bags | `fill` | 75% | Share of the bag volume that holds sand |
| bags | `per_pack`, `pack_price` | 1, 0 | Bags per pack and price per pack |
| sand | `density` | 1600 kg/m3 | Dry sand density |
| sand | `price_per_tonne`, `delivery` | 0, 0 | Sand price and delivery charge |
| site | `flood_depth` | none | Water depth to protect against |
| site | `freeboard` | 0.30 m | Margin above the flood depth |
| site | `stagger` | yes | Offset alternate courses |
| site | `spare` | 5% | Extra bags to order |
| site | `crew`, `bags_per_hour` | 1, 20 | Labour rate for crew hours |
| wall | `length`, `thickness`, `height` | required | Wall dimensions |
| wall | `openings` | none | Comma-separated gap widths, e.g. `0.9 m, 0.6 m` |

## Commands

| Command | What it does |
|---|---|
| `report PLAN [--json]` | Print the full plan summary, or JSON for other tools |
| `lint PLAN` | Check a plan; exit 2 and print `file:line: message` on any error |
| `diagram PLAN [--wall NAME] [--courses N]` | Draw each wall's courses |
| `check PLAN [--max-bags N] [--max-cost AMOUNT]` | Gate on limits and flood clearance |

Exit codes: `0` success, `1` a check failed, `2` the plan is invalid or unreadable.

### Using it in CI

```bash
python3 sandbag.py check plans/flood-barrier.txt --max-bags 200 --max-cost 900
```

## How to test

The tests use `pytest`, which is the only third-party dependency.

```bash
python3 -m pip install pytest
python3 -m pytest tests/
```

The tests cover unit parsing, line-numbered lint errors, bag counts with and
without staggering, thickness and openings, pack rounding, cost totals, freeboard
warnings, diagrams, JSON output and CLI exit codes.

## Assumptions

- Bags lie lengthwise along the wall, and the `width` dimension runs across it.
- Staggered courses use one cut bag per across-unit for the offset ends.
- Sand is priced on the bags you fill, not on the spares.
- Sand is charged by the exact tonne, not rounded up to whole loads.
- Labour is `needed bags ÷ bags per hour ÷ crew`.

This is a planning aid. Check the wall height and the flood level with your local
authority or an engineer before relying on it.

## Project layout

```text
sandbag.py              the planner and CLI (single file)
examples/garage.txt     a worked example plan
tests/test_sandbag.py   pytest suite
```

## License

No license file is included. Add one before you publish the repository.
