import json
from pathlib import Path
from types import ModuleType
import asyncio

import pytest
import yaml

from agent_jit.catalog import task
from agent_jit.configuration import validate_config
from agent_jit.runner import evaluate_config
from agent_jit.evaluation import evaluate

ROOT = Path(__file__).resolve().parents[1]


def planner_config(tmp_path):
    data = yaml.safe_load((ROOT / "configs/planner-real-smoke.yaml").read_text())
    data["ledger"] = str(tmp_path / "ledger.json")
    return data


def test_paper_scale_settings_validate_without_fixed_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    cfg = planner_config(tmp_path)
    cfg.update(
        budget_usd=50,
        candidates=64,
        max_candidate_attempts=128,
        max_requests=10000,
        trials=10,
    )
    validate_config(cfg, [task(ROOT, "v1/dashdish-1")], ROOT)
    assert not Path(cfg["ledger"]).exists()  # no request or reservation


@pytest.mark.parametrize(
    "change,match",
    [
        ({"budget_usd": -1}, "budget_usd"),
        ({"candidates": 0}, "candidates"),
        ({"candidate_target": "typo"}, "candidate_target"),
        ({"candidate_temperature": 3}, "candidate_temperature"),
        ({"model": "unsupported"}, "Unsupported live model"),
        ({"replay_candidates": ["runs/x"]}, "replay_candidates must map"),
        ({"replay_candidates": {"v1/gomail-1": "runs/x"}}, "task not in tasks"),
        ({"replay_candidates": {"v1/dashdish-1": "runs/missing"}}, "has no candidates.json"),
    ],
)
def test_bad_configuration_fails_before_any_trial(tmp_path, monkeypatch, change, match):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    cfg = planner_config(tmp_path)
    cfg.update(change)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match=match):
        evaluate_config(ROOT, path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_credentials_fail_before_browser_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = planner_config(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        evaluate_config(ROOT, path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_custom_task_and_verifier_can_run_without_paper_catalog(tmp_path, monkeypatch):
    catalog = tmp_path / "tasks.json"
    catalog.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "local-count",
                        "application": "myshop",
                        "task_details": "Count items",
                        "initial_url": "https://fixture.test/",
                        "verifier": {
                            "type": "custom",
                            "callable": "custom_fixture:verify",
                        },
                    }
                ]
            }
        )
    )
    t = task(tmp_path, "local-count", catalog)
    module = ModuleType("custom_fixture")
    module.verify = lambda task, text, state: {
        "correct": text == "3",
        "score": float(text == "3"),
    }
    import sys

    monkeypatch.setitem(sys.modules, "custom_fixture", module)
    result = asyncio.run(evaluate(t, "3", None, "json"))
    assert result["correct"] and result["score"] == 1
    assert t["benchmark"] == "custom"


def test_custom_scheduler_can_collect_traces_before_caches_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-not-a-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = yaml.safe_load((ROOT / "configs/scheduler-real-smoke.yaml").read_text())
    cfg.update(
        model="gemini-2.5-flash",
        mini_model="gemini-2.5-flash",
        predictor_model="gemini-2.5-flash",
        profile_model="new-execution-profile",
        evaluator="json",
        strategy="serial",
        ledger=str(tmp_path / "ledger.json"),
    )
    tasks = [{"benchmark": "custom", "application": "myshop"}]
    validate_config(cfg, tasks, tmp_path)
    assert not (tmp_path / "ledger.json").exists()
    cfg["strategy"] = "jit"
    with pytest.raises(ValueError, match="Latency profile does not exist"):
        validate_config(cfg, tasks, tmp_path)


def test_historical_evaluator_requires_its_own_provider_key(tmp_path, monkeypatch):
    cfg = planner_config(tmp_path)
    cfg.update(model="gemini-2.5-pro", evaluator="historical")
    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-not-a-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        validate_config(cfg, [task(ROOT, "v1/dashdish-1")], ROOT)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-not-a-key")
    validate_config(cfg, [task(ROOT, "v1/dashdish-1")], ROOT)


def test_missing_custom_verifier_fails_before_spending(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    catalog = tmp_path / "tasks.json"
    entry = {
        "id": "custom",
        "application": "dashdish",
        "task_details": "List stores",
        "initial_url": "https://example.test/",
        "verifier": {"type": "custom", "callable": "missing_test_verifier:verify"},
    }
    catalog.write_text(json.dumps({"tasks": [entry]}))
    cfg = planner_config(tmp_path)
    cfg.update(task_catalog=str(catalog), tasks=["custom"])
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="Custom verifier.*cannot be imported"):
        evaluate_config(ROOT, path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not Path(cfg["ledger"]).exists()


def test_custom_verifier_can_be_imported_from_artifact_root(tmp_path):
    from agent_jit.evaluation import custom_verifier

    (tmp_path / "local_verifier_for_test.py").write_text(
        "def verify(task, text, state):\n    return {'correct': False, 'score': 0.25}\n"
    )
    verifier = custom_verifier({"callable": "local_verifier_for_test:verify"}, tmp_path)
    assert verifier({}, "", None) == {"correct": False, "score": 0.25}


def test_fixed_scheduler_does_not_require_unused_predictor_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-not-a-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = yaml.safe_load((ROOT / "configs/scheduler-real-smoke.yaml").read_text())
    cfg.update(
        model="gemini-2.5-flash",
        mini_model="gemini-2.5-flash",
        predictor_model="gpt-4.1",
        strategy="serial",
        evaluator="json",
        ledger=str(tmp_path / "ledger.json"),
    )
    validate_config(cfg, [{"benchmark": "custom", "application": "fixture"}], tmp_path)
    cfg["strategy"] = "jit"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_config(
            cfg, [{"benchmark": "custom", "application": "fixture"}], tmp_path
        )
