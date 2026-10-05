import json
from pathlib import Path

import pytest

from agent_jit.planner_results import analyze_planner, classify, planner_results

ROOT = Path(__file__).resolve().parents[1]


def test_table1_matches_all_98_primary_cells():
    result = planner_results(ROOT)
    assert len(result["rows"]) == 14
    for row, reference in zip(result["rows"], result["published_rows"], strict=True):
        assert (row["dimension"], row["label"]) == (
            reference["dimension"],
            reference["label"],
        )
        for method, expected in reference["methods"].items():
            assert f"{row[method]['latency_s']:.1f}" == f"{expected['latency_s']:.1f}"
            assert (
                f"{100 * row[method]['correctness']:.0f}"
                == f"{100 * expected['correctness']:.0f}"
            )
        assert f"{row['best_cost_spread_s']:.1f}" == f"{reference['table_range_s']:.1f}"
    assert result["model_macro_means"]["loop-baseline"]["latency_s"] == pytest.approx(
        122.1219764109011
    )
    assert result["headline_speedup"] == pytest.approx(10.409859707959642)
    assert result["headline_accuracy_gain_pp"] == pytest.approx(27.9315982649316)


def test_dashdish_group_aggregation():
    result = planner_results(ROOT)
    row = next(r for r in result["rows"] if r["label"] == "dashdish")
    assert row["best"]["input_groups"] == 27
    assert row["best"]["latency_s"] == pytest.approx(14.525051484873265)
    assert row["best"]["correctness"] == 1
    assert round(row["best_cost_spread_s"], 1) == 35.3
    assert round(100 * row["loop-baseline"]["correctness"]) == 65
    assert round(row["accuracy_gain_pp"]) == 35
    selected = [result["groups"][i] for i in row["best"]["snapshot_group_indices"]]
    assert all(g["method"] == "best" and g["trials"] == 3 for g in selected)
    assert min(selected, key=lambda g: g["latency_s"])["task"] == "dashdish-custom-8"
    assert max(selected, key=lambda g: g["latency_s"])["task"] == "dashdish-custom-4"


def test_saved_group_weighting_and_output(tmp_path):
    result = planner_results(ROOT)
    model_rows = [r for r in result["rows"] if r["dimension"] == "model"]
    assert [r["loop-baseline"]["input_groups"] for r in model_rows] == [27, 36, 36]
    assert result["model_macro_means"]["loop-tools-baseline"][
        "latency_s"
    ] == pytest.approx(80.09491867210433)
    snapshot = json.loads(
        (ROOT / "data/recorded/planner/table1-snapshot.json").read_text()
    )
    assert len(snapshot["groups"]) == len(result["groups"]) == 435
    for original, group in zip(snapshot["groups"], result["groups"], strict=True):
        assert all(group[key] == value for key, value in original.items())
    analyze_planner(ROOT, tmp_path)
    assert json.loads((tmp_path / "planner-table1.json").read_text()) == result
    assert len((tmp_path / "planner-table1.csv").read_text().splitlines()) == 15
    assert {p.name for p in tmp_path.iterdir()} == {
        "planner-table1.json",
        "planner-table1.csv",
        "planner-table1.md",
    }


def test_final_paper_task_tiers():
    reference = json.loads(
        (ROOT / "data/recorded/planner/table1-reference.json").read_text()
    )
    assert [
        classify(n, reference["thresholds"]["cardinality"]) for n in [1, 2, 4, 5]
    ] == ["C-Low", "C-Medium", "C-Medium", "C-High"]
    assert [
        classify(n, reference["thresholds"]["length"]) for n in [1, 5, 6, 8, 9]
    ] == ["T-Short", "T-Short", "T-Medium", "T-Medium", "T-Long"]
    with pytest.raises(ValueError, match="stratum"):
        classify(0, reference["thresholds"]["length"])
