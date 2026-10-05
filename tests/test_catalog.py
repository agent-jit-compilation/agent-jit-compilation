from pathlib import Path
import json
from collections import Counter

from agent_jit.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_paper_subset_and_verifier_coverage():
    catalog = json.loads((ROOT / "benchmarks/tasks.json").read_text())
    tasks = catalog["tasks"]
    assert len(tasks) == 37
    assert len({(t["benchmark"], t["id"]) for t in tasks}) == 37
    assert sum(t["is_added"] for t in tasks) == 18
    assert sum(t["benchmark"] == "REAL" for t in tasks) == 27
    for task in tasks:
        assert task["task_details"]
        assert (ROOT / task["definition_path"]).is_file()
        if task["benchmark"] == "REAL":
            assert (ROOT / task["verifier"]["script"]).is_file()
        else:
            assert task["verifier"]["configuration"]["eval"]["eval_types"]


def test_cli_exposes_the_paper_planner_and_scheduler_task_sets(capsys):
    assert main(["tasks", "--component", "planner", "--json"]) == 0
    planner = json.loads(capsys.readouterr().out)
    assert len(planner) == 37
    assert Counter(t["application"] for t in planner) == {
        "dashdish": 9,
        "gomail": 9,
        "omnizon": 9,
        "gitlab": 5,
        "reddit": 5,
    }

    assert main(["tasks", "--component", "scheduler", "--json"]) == 0
    scheduler = json.loads(capsys.readouterr().out)
    assert len(scheduler) == 27
    assert all(t["benchmark"] == "REAL" for t in scheduler)
    assert Counter(t["oracle_schedule"] for t in scheduler) == {
        "serial": 9,
        "parallel": 9,
        "hedge": 9,
    }


def test_cli_rejects_a_planner_only_task_in_the_scheduler_set(capsys):
    assert main(["tasks", "webarena-308", "--component", "scheduler"]) == 2
    assert "not in the scheduler task set" in capsys.readouterr().err
