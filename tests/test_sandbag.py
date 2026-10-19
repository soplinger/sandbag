import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbag  # noqa: E402

EXAMPLE = ROOT / "examples" / "garage.txt"

PLAN = """\
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

[wall side]
length = 2.5 m
thickness = 0.40 m
height = 0.45 m
openings = 0.9 m
"""


def _plan(text=PLAN):
    plan, issues = sandbag.parse_plan(text)
    assert issues == []
    return plan


def _wall(**overrides):
    wall = {"name": "w", "length": D("3.2"), "thickness": D("0.40"),
            "height": D("0.60"), "openings": []}
    wall.update(overrides)
    return wall


def test_parse_length_units_convert_to_metres():
    assert sandbag.parse_length("0.6 m") == D("0.6")
    assert sandbag.parse_length("60cm") == D("0.6")
    assert sandbag.parse_length("600 mm") == D("0.6")
    assert sandbag.parse_length("2 ft") == D("0.6096")
    assert sandbag.parse_length("24 in") == D("0.6096")


def test_parse_length_rejects_unitless_and_zero():
    with pytest.raises(ValueError, match="not a length"):
        sandbag.parse_length("0.6")
    with pytest.raises(ValueError, match="greater than zero"):
        sandbag.parse_length("0 m")
    assert sandbag.parse_length("0 m", allow_zero=True) == 0


def test_parse_percent_and_money():
    assert sandbag.parse_percent("75%") == D("0.75")
    assert sandbag.parse_percent("5") == D("0.05")
    with pytest.raises(ValueError, match="more than 100"):
        sandbag.parse_percent("150%")
    assert sandbag.parse_money("14.90") == D("14.90")
    with pytest.raises(ValueError, match="not an amount"):
        sandbag.parse_money("$14.90")


def test_lint_reports_unknown_key_with_line_number():
    text = PLAN.replace("crew = 2", "crew = 2\ncolour = red")
    issues = sandbag.lint_text(text)
    assert len(issues) == 1
    assert issues[0].message == "unknown key 'colour' in [site]"
    assert issues[0].line == text.splitlines().index("colour = red") + 1


def test_lint_reports_missing_required_key_and_missing_wall():
    text = PLAN.replace("height = 0.15 m\n", "").split("[wall threshold]")[0]
    messages = [i.message for i in sandbag.lint_text(text)]
    assert any("missing required key 'height'" in m for m in messages)
    assert any("need at least one [wall NAME]" in m for m in messages)


def test_lint_reports_duplicate_key_and_bad_value_lines():
    text = PLAN.replace("crew = 2", "crew = 2\ncrew = 3").replace("spare = 5%", "spare = lots")
    issues = sandbag.lint_text(text)
    by_message = {i.message: i.line for i in issues}
    assert any("duplicate key 'crew'" in m for m in by_message)
    assert any(m.startswith("spare:") for m in by_message)
    spare_line = text.splitlines().index("spare = lots") + 1
    assert [i.line for i in issues if i.message.startswith("spare:")] == [spare_line]


def test_opening_longer_than_wall_is_an_error():
    text = PLAN.replace("openings = 0.9 m", "openings = 3 m")
    issues = sandbag.lint_text(text)
    assert any("not shorter than wall length" in i.message for i in issues)


def test_example_file_lints_clean():
    assert sandbag.lint_text(EXAMPLE.read_text(encoding="utf-8")) == []


def test_staggered_wall_counts_extra_bag_on_offset_courses():
    result = sandbag.wall_result(_wall(), _plan().bag, stagger=True)
    # 4 courses: two full (6 bags each), two offset (7 bags each, one cut for the ends)
    assert result.courses == 4
    assert result.offset_courses == 2
    assert result.bags == 26


def test_unstaggered_wall_has_no_extra_bags():
    result = sandbag.wall_result(_wall(), _plan().bag, stagger=False)
    assert result.bags == 24


def test_openings_reduce_net_length():
    side = [w for w in _plan().walls if w["name"] == "side"][0]
    result = sandbag.wall_result(side, _plan().bag, stagger=True)
    assert result.net_length == D("1.6")
    assert result.bays == 3
    assert result.bags == 10


def test_thickness_adds_bags_across():
    result = sandbag.wall_result(_wall(thickness=D("0.80")), _plan().bag, stagger=False)
    assert result.across == 2
    assert result.bags == 48


def test_summary_totals_and_pack_rounding():
    summary = sandbag.summarise(_plan())
    assert summary["bags_needed"] == 36
    assert summary["spare_bags"] == 2
    assert summary["bags_to_order"] == 38
    assert summary["packs"] == 1
    assert summary["sand_m3"] == D("0.972")
    assert summary["bag_cost"] == D("14.90")
    assert summary["sand_cost"] == D("59.10")
    assert summary["total_cost"] == D("119.00")
    assert summary["crew_hours"] == D("1.00")


def test_pack_rounding_rounds_up_partial_packs():
    plan = _plan(PLAN.replace("per_pack = 50", "per_pack = 10"))
    assert sandbag.summarise(plan)["packs"] == 4


def test_freeboard_warnings_flag_short_walls():
    warnings = sandbag.summarise(_plan())["warnings"]
    assert len(warnings) == 2
    assert all("below flood depth plus freeboard" in w for w in warnings)


def test_no_freeboard_warning_when_walls_are_tall_enough():
    plan = _plan(PLAN.replace("flood_depth = 0.45 m", "flood_depth = 0.10 m"))
    assert sandbag.summarise(plan)["warnings"] == []


def test_diagram_marks_offset_courses_and_limits_height():
    text = sandbag.diagram(_wall(), _plan().bag, stagger=True, max_courses=3)
    rows = [line for line in text.splitlines() if line.startswith("course")]
    assert len(rows) == 3
    assert rows[1].count("~") == 8  # course 2 is the first offset course, drawn from the bottom
    assert "1 more course(s) not shown" in text


def test_cli_report_json_output(tmp_path, capsys):
    path = tmp_path / "plan.txt"
    path.write_text(PLAN, encoding="utf-8")
    assert sandbag.main(["report", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["bags_to_order"] == 38
    assert data["total_cost"] == "119.00"


def test_cli_lint_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.txt"
    good.write_text(PLAN, encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text(PLAN.replace("crew = 2", "crew = two"), encoding="utf-8")
    assert sandbag.main(["lint", str(good)]) == 0
    assert sandbag.main(["lint", str(bad)]) == 2
    assert f"{bad}:" in capsys.readouterr().out


def test_cli_check_gates_on_bag_and_cost_limits(tmp_path, capsys):
    path = tmp_path / "plan.txt"
    path.write_text(PLAN.replace("flood_depth = 0.45 m", "flood_depth = 0.10 m"), encoding="utf-8")
    assert sandbag.main(["check", str(path), "--max-bags", "100", "--max-cost", "500"]) == 0
    assert sandbag.main(["check", str(path), "--max-bags", "10"]) == 1
    assert sandbag.main(["check", str(path), "--max-cost", "50"]) == 1
    assert "FAIL: bags to order 38 exceed limit 10" in capsys.readouterr().out


def test_cli_check_fails_on_freeboard_warning():
    assert sandbag.main(["check", str(EXAMPLE)]) == 1
