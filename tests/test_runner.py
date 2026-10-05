"""Failure injection tests of the result-writing path, not benchmark executions."""

import json
from pathlib import Path
import subprocess
import yaml
import pytest
from agent_jit import runner

ROOT = Path(__file__).resolve().parents[1]


def config_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "adapter": "fixture:run",
                "method": "failure-fixture",
                "method_revision": "fixture",
                "tasks": ["dashdish-custom-5"],
                "trials": 1,
                "timeout_s": 1,
                "evaluator": "json",
            }
        )
    )
    return p


def test_worker_crash_retained_and_no_usage_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "run_isolated", lambda *a, **k: 9)
    report = runner.evaluate_config(ROOT, config_file(tmp_path), tmp_path / "out")
    assert report["attempts"] == 1 and report["statuses"] == ["setup_error"]
    r = json.loads(Path(report["result_file"]).read_text())
    assert not r["success"] and r["score"] == 0
    assert r["usage"]["estimated_cost_usd"] is None
    assert r["usage"]["prompt_tokens"] is None
    assert runner.validate_results(Path(report["result_file"]))["accuracy"] == 0


def test_hard_deadline_preserves_timeout_and_phase(tmp_path, monkeypatch):
    def timed_out(command, **kwargs):
        request = Path(command[-2])
        request.with_name("phase.json").write_text(
            json.dumps({"phase": "agent", "setup_s": 0, "reset": {"method": "fixture"}})
        )
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(runner, "run_isolated", timed_out)
    report = runner.evaluate_config(ROOT, config_file(tmp_path), tmp_path / "out")
    r = json.loads(Path(report["result_file"]).read_text())
    assert r["status"] == "timeout" and not r["success"]
    assert r["reset"]["method"] == "fixture"
    assert r["timing"]["online_s"] >= 0


def test_source_archive_revision_tracks_code_and_excludes_private_runtime_files(
    tmp_path,
):
    from agent_jit.runner import revision

    (tmp_path / "agent.py").write_text("value = 1\n")
    first = revision(tmp_path)
    assert first.startswith("source-archive; checkout-sha256:")
    (tmp_path / ".env").write_text("fixture private content")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs/result.json").write_text("fixture result")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/generated.py").write_text("fixture dependency")
    assert revision(tmp_path) == first
    (tmp_path / "agent.py").write_text("value = 2\n")
    assert revision(tmp_path) != first


def test_cross_model_profile_is_recorded_even_when_worker_fails(tmp_path, monkeypatch):
    import agent_jit.configuration as configuration

    cfg = config_file(tmp_path)
    data = yaml.safe_load(cfg.read_text())
    data.update(
        adapter="agent_jit.adapters.scheduler:run",
        strategy="jit",
        model="gpt-4.1-mini",
        profile_model="gpt-4.1",
    )
    cfg.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(configuration, "validate_config", lambda *args: None)
    monkeypatch.setattr(runner, "run_isolated", lambda *args, **kwargs: 9)
    report = runner.evaluate_config(ROOT, cfg, tmp_path / "out")
    record = json.loads(Path(report["result_file"]).read_text())
    assert record["cross_model_profile"] is True
    assert (
        report["complete"] is True
    )  # All requested attempts finished, albeit unsuccessfully.
    assert runner.validate_results(Path(report["result_file"]))["complete"] is True


@pytest.mark.parametrize("contents", ["", "[]", "tasks: [", "null", "42"])
def test_malformed_config_fails_before_creating_output(tmp_path, contents):
    path = tmp_path / "config.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError, match="configuration|Configuration"):
        runner.evaluate_config(ROOT, path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "update",
    [
        {"tasks": [{"id": "dashdish-custom-5"}]},
        {"tasks": [None]},
        {"tasks": [""]},
        {"tasks": ["dashdish-custom-5", "v1/dashdish-1"]},
        {"timeout_s": True},
    ],
)
def test_invalid_settings_fail_before_creating_output(tmp_path, update):
    path = config_file(tmp_path)
    config = yaml.safe_load(path.read_text())
    config.update(update)
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        runner.evaluate_config(ROOT, path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
