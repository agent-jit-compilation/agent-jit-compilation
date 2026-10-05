"""Regression coverage for runner accounting and application-extension paths."""

import asyncio
import json
from pathlib import Path
import warnings

import pytest
import yaml

from agent_jit import runner
from agent_jit.budget import BudgetExceeded, Ledger
from agent_jit.catalog import task
from agent_jit.webarena import validate_evaluation
from agent_jit.webarena_environment import Snapshot, wait_ready, preflight

ROOT = Path(__file__).resolve().parents[1]


def test_swallowed_budget_error_is_unscored_and_stops_campaign(tmp_path, monkeypatch):
    config = {
        "adapter": "fixture:run",
        "method": "fixture",
        "method_revision": "fixture",
        "tasks": ["dashdish-custom-5"],
        "trials": 3,
        "timeout_s": 1,
        "evaluator": "json",
        "budget_usd": 1,
        "max_requests": 1,
        "ledger": str(tmp_path / "ledger.json"),
    }
    file = tmp_path / "config.yaml"
    file.write_text(yaml.safe_dump(config))

    def worker(command, **kwargs):
        ledger = Ledger(Path(config["ledger"]), 1, 1)
        ledger.reserve("gpt-4.1-mini", {"messages": []}, 1)
        try:
            ledger.reserve("gpt-4.1-mini", {"messages": []}, 1)
        except BudgetExceeded:
            pass  # Browser workers may swallow model exceptions.
        Path(command[-1]).write_text(
            json.dumps({"status": "failure", "success": False, "score": 0})
        )
        return 0

    monkeypatch.setattr(runner, "run_isolated", worker)
    result = runner.evaluate_config(ROOT, file, tmp_path / "out")
    assert result["attempts"] == 1 and result["not_started"] == 2
    assert result["accuracy"] is None and result["scored_attempts"] == 0
    assert result["complete"] is False
    assert json.loads((tmp_path / "out/summary.json").read_text())["complete"] is False
    row = json.loads(Path(result["result_file"]).read_text())
    assert row["status"] == "budget_exhausted" and row["score"] is None
    assert row["usage"]["budget_denials"][0]["reason"] == "request_limit"
    validated = runner.validate_results(Path(result["result_file"]))
    assert validated["accuracy"] is None and validated["complete"] is False
    with pytest.raises(BudgetExceeded):
        runner.evaluate_config(ROOT, file, tmp_path / "not-started")
    assert not (tmp_path / "not-started").exists()


def test_new_webarena_tasks_load_without_changing_paper_catalog(tmp_path):
    catalog = tmp_path / "tasks.json"
    catalog.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "csvkit", "benchmark": "WebArena", "webarena_task_id": 312},
                    {
                        "id": "infeasible",
                        "benchmark": "WebArena",
                        "webarena_task_id": 726,
                    },
                ]
            }
        )
    )
    entry = task(ROOT, "csvkit", catalog)
    assert entry["benchmark"] == "WebArena" and entry["application"] == "gitlab"
    assert (
        entry["verifier"]["configuration"]["eval"]["reference_answers"]["exact_match"]
        == "Christopher Groskopf"
    )
    assert "csvkit" in entry["task_details"]
    validate_evaluation(entry["verifier"]["configuration"])
    with pytest.raises(ValueError, match="fuzzy_match/infeasible"):
        validate_evaluation(
            task(ROOT, "infeasible", catalog)["verifier"]["configuration"]
        )


def test_gitlab_requires_stable_login_readiness(monkeypatch):
    from agent_jit import webarena_environment as env

    pages = iter(
        ["username-field", "502", "username-field", "username-field", "username-field"]
    )
    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return next(pages).encode()

    def open_url(url, **kwargs):
        seen.append(url)
        return Response()

    monkeypatch.setattr(env, "urlopen", open_url)
    monkeypatch.setattr(env.time, "monotonic", lambda: 0)
    monkeypatch.setattr(env.time, "sleep", lambda _: None)
    wait_ready("gitlab", "http://127.0.0.1:18023", 60)
    assert seen == ["http://127.0.0.1:18023/users/sign_in"] * 5


def test_snapshot_uses_worker_limit_shared_memory_and_ipv4(monkeypatch):
    from agent_jit import webarena_environment as env

    calls = []

    def docker(self, *args, **kw):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:fixture", "Architecture": "amd64"}])
        if args[0] == "create":
            return "owned-fixture"
        return ""

    monkeypatch.setattr(Snapshot, "docker", docker)
    monkeypatch.setattr(env, "wait_ready", lambda site, url, deadline: None)
    snapshot = Snapshot(
        {"sites": {"gitlab": {"port": 18023, "puma_workers": 3, "shm_size": "512m"}}},
        ["gitlab"],
        "fixture",
    )
    snapshot.start()
    create = next(c for c in calls if c[0] == "create")
    assert create[create.index("--shm-size") + 1] == "512m"
    assert "127.0.0.1:18023:18023" in create
    assert snapshot.endpoints["gitlab"] == "http://127.0.0.1:18023"
    assert any("worker_processes" in c[-1] and "= 3" in c[-1] for c in calls)
    assert ("exec", "owned-fixture", "gitlab-ctl", "restart", "puma") in calls


def test_missing_image_rejected_without_creating_container(monkeypatch):
    calls = []
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    def docker(self, *args, **kw):
        calls.append(args)
        if args[0] == "info":
            return '{"OSType":"linux"}'
        if args[0] == "context":
            return '[{"Endpoints":{"docker":{"Host":"unix:///fixture.sock"}}}]'
        raise RuntimeError("image not found")

    monkeypatch.setattr(Snapshot, "docker", docker)
    with pytest.raises(RuntimeError, match="image not found"):
        preflight({}, ["gitlab"])
    assert not any(c[0] in ("create", "start") for c in calls)


def test_sparse_profile_is_reported_before_prediction(tmp_path, monkeypatch):
    from agent_jit import predict
    from blast_scheduler.online.scheduler import Scheduler, SchedulerPlan, ElementCount

    class Client:
        def __init__(self, *a):
            pass

        def close(self):
            pass

    def sequential(self, task):
        return SchedulerPlan(
            explanation="fixture",
            elements=[
                ElementCount(
                    element_name=self.valid_elements[0],
                    count=1,
                    required=True,
                    destination_page="main",
                )
            ],
        )

    monkeypatch.setattr(predict, "BudgetedResponses", Client)
    monkeypatch.setattr(Scheduler, "get_sequential_usage", sequential)
    monkeypatch.setattr(Scheduler, "get_parallel_usage", lambda *a: None)
    with warnings.catch_warnings(record=True) as warnings_seen:
        result = predict.predict(
            ROOT,
            "fixture",
            "dashdish",
            "gpt-4.1",
            "gpt-4.1",
            "mean",
            0,
            5,
            tmp_path / "ledger.json",
            1,
            tmp_path / "out",
        )
    coverage = result["profile_coverage"]
    assert len(coverage["schema_elements"]) > len(coverage["profiled_elements"])
    assert coverage["unprofiled_elements"] and warnings_seen
    assert json.loads((tmp_path / "out/profile-coverage.json").read_text()) == coverage


def test_plan_type_errors_reference_user_source_and_ties_are_explicit(tmp_path):
    from agent_jit.planner import check, rank

    registry = ROOT / "data/registries/dashdish.json"
    plan = tmp_path / "bad.py"
    plan.write_text(
        "stores = await list_all_stores(detailed=True)\nresult = stores.items[0].review_count\n"
    )
    result = check(plan, registry, {"page_type": "home"})
    assert not result["valid"] and "Line 2:" in result["error"]
    a = ROOT / "examples/plans/valid.py"
    b = tmp_path / "same.py"
    b.write_text(a.read_text())
    result = rank([a, b], registry, {"page_type": "home"})
    assert result["tied_candidates"] == [str(a), str(b)] and result["selected"] == str(
        a
    )


def test_login_failure_keeps_diagnostic_log_after_private_state_is_removed(
    tmp_path, monkeypatch
):
    from agent_jit import process, webarena
    from agent_jit.webarena_environment import login
    import tempfile

    monkeypatch.setattr(webarena, "verify", lambda *args: None)

    def failed(*args, **kwargs):
        kwargs["log"].write("fixture login failed\n")
        return 1

    monkeypatch.setattr(process, "run_isolated", failed)
    log = tmp_path / "auto-login.log"
    with tempfile.TemporaryDirectory() as profile:
        with pytest.raises(RuntimeError, match=str(log)):
            asyncio.run(
                login(
                    None,
                    ["gitlab"],
                    {"gitlab": "http://127.0.0.1:8023"},
                    root=tmp_path,
                    checkout=tmp_path,
                    auth_folder=Path(profile) / ".auth",
                    log_path=log,
                )
            )
    assert log.read_text() == "fixture login failed\n"


def test_experiment_import_does_not_load_ambient_dotenv():
    pytest.importorskip("browser_use")
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    code = """
import inspect
import dotenv
from unittest.mock import patch
original = dotenv.load_dotenv
def checked_load(*args, **kwargs):
    # Third-party Browser-Use also calls dotenv; CI disables those calls.
    caller = inspect.currentframe().f_back.f_globals.get('__name__', '')
    if caller == 'experiments.evaluate_codegen':
        raise AssertionError('experiment loaded ambient dotenv during import')
    return original(*args, **kwargs)
with patch.object(dotenv, 'load_dotenv', checked_load):
    import experiments.evaluate_codegen
"""
    subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_scheduler_warns_on_cross_model_profile(tmp_path, monkeypatch):
    from agent_jit.configuration import validate_config

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    config = yaml.safe_load((ROOT / "configs/scheduler-real-smoke.yaml").read_text())
    config["ledger"] = str(tmp_path / "ledger.json")
    tasks = [task(ROOT, "v1/dashdish-1")]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validate_config(config, tasks, ROOT)
    assert not caught  # Dated and undated names refer to the same model.
    config["model"] = "gpt-4.1-mini-2025-04-14"
    with pytest.warns(UserWarning, match="differs from latency profile"):
        validate_config(config, tasks, ROOT)


def test_capture_produces_schema_builder_inputs(tmp_path):
    pytest.importorskip("patchright")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from scripts.capture_page import capture

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><button id='ready'>Search</button></body></html>"
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "capture"
    try:
        report = asyncio.run(
            capture(f"http://127.0.0.1:{server.server_port}", output, wait_for="#ready")
        )
        manifest = json.loads((output / "pages.json").read_text())[0]
        assert report["model_calls"] == 0
        assert manifest["url"] == report["url"]
        assert manifest["image_folder"] == manifest["html_folder"] == "."
        assert "Search" in (output / "page.html").read_text()
        assert (output / "page.png").read_bytes().startswith(b"\x89PNG")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
