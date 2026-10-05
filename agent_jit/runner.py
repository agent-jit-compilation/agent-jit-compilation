"""Sequential isolated trials; failed/timeout attempts remain in the denominator."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
import yaml
from .model_catalog import cross_model_profile
from .catalog import task as get_task
from .results import TrialResult
from .process import run_isolated


def revision(root):
    """Identify executed checkout contents, including uncommitted source changes."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        top = None
    if (
        top is not None
        and top.returncode == 0
        and Path(top.stdout.strip()).resolve() == Path(root).resolve()
    ):
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
        )
        base = p.stdout.strip() if p.returncode == 0 else "uncommitted"
        paths = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=root,
            )
            .decode()
            .split("\0")
        )
    else:
        # A source distribution has no Git metadata. Hash its files while pruning
        # credentials, runtime output, environments and caches as the checkout does.
        base = "source-archive"
        excluded = {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".downloads",
            ".cache",
            ".auth",
            "build",
            "dist",
            "runs",
        }
        paths = []
        for directory, subdirs, files in os.walk(root):
            subdirs[:] = [
                d for d in subdirs if d not in excluded and not d.endswith(".egg-info")
            ]
            for name in files:
                if (
                    (name.startswith(".env") and name != ".env.example")
                    or name == ".DS_Store"
                    or name.endswith((".pyc", ".json.lock"))
                ):
                    continue
                paths.append(str((Path(directory) / name).relative_to(root)))
    digest = hashlib.sha256()
    for relative in sorted(set(filter(None, paths))):
        path = Path(root) / relative
        digest.update(relative.encode() + b"\0")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"deleted")
    return f"{base}; checkout-sha256:{digest.hexdigest()}"


def evaluate_config(root: Path, config_path: Path, output: Path):
    try:
        config = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a YAML mapping of settings")
    required = [
        "adapter",
        "method",
        "method_revision",
        "tasks",
        "trials",
        "timeout_s",
        "evaluator",
    ]
    for key in required:
        if key not in config:
            raise ValueError(f"Missing configuration key: {key}")
    if (
        type(config["trials"]) is not int
        or config["trials"] < 1
        or type(config["timeout_s"]) not in (int, float)
        or not 0 < config["timeout_s"] < float("inf")
    ):
        raise ValueError("Invalid trial count or timeout")
    if not isinstance(config.get("save_browser_history", False), bool):
        raise ValueError("save_browser_history must be a boolean")
    if config["evaluator"] not in ("json", "openai", "historical", "webarena_native"):
        raise ValueError(
            "Evaluator must be json, openai, historical, or webarena_native"
        )
    if (
        not isinstance(config["tasks"], list)
        or not config["tasks"]
        or any(not isinstance(t, str) or not t.strip() for t in config["tasks"])
        or len(set(config["tasks"])) != len(config["tasks"])
    ):
        raise ValueError("Choose a nonempty list of distinct task IDs")
    from .configuration import validate_config

    selected = [get_task(root, t, config.get("task_catalog")) for t in config["tasks"]]
    if len({t["id"] for t in selected}) != len(selected):
        raise ValueError("Task IDs and aliases must refer to distinct tasks")
    validate_config(config, selected, root)
    webarena_tasks = [t for t in selected if t["benchmark"] == "WebArena"]
    if webarena_tasks:
        if (
            len(webarena_tasks) != len(selected)
            or config["evaluator"] != "webarena_native"
        ):
            raise ValueError(
                "Use a separate WebArena campaign with evaluator: webarena_native"
            )
        from .webarena import verify, validate_evaluation
        from .webarena_environment import preflight

        checkout = Path(config["webarena"]["checkout"]).resolve()
        verify(root, checkout)
        config["webarena"]["checkout"] = str(checkout)
        for entry in webarena_tasks:
            validate_evaluation(entry["verifier"]["configuration"])
        preflight(
            config["webarena"],
            sorted(
                {
                    site
                    for t in webarena_tasks
                    for site in t["verifier"]["configuration"]["sites"]
                }
            ),
        )
        if config["webarena"].get("nltk_data"):
            config["webarena"]["nltk_data"] = str(
                Path(config["webarena"]["nltk_data"]).resolve()
            )
    elif config["evaluator"] == "webarena_native":
        raise ValueError("webarena_native requires WebArena tasks")
    if config.get("ledger"):
        from .budget import Ledger

        Ledger(
            Path(config["ledger"]),
            config["budget_usd"],
            config.get("max_requests", 128),
        ).assert_available()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "results.jsonl"
    if result_path.exists():
        raise ValueError("Use a new output directory to avoid mixing campaigns")
    if config.get("ledger"):
        config["ledger"] = str(Path(config["ledger"]).resolve())
    for key in ("latency_profile", "schema_root", "tool_registry", "task_catalog"):
        if config.get(key):
            config[key] = str(Path(config[key]).resolve())
    records = []
    stop_reason = None
    for task in selected:
        if stop_reason:
            break
        for trial in range(config["trials"]):
            if config.get("ledger"):
                from .budget import Ledger, BudgetExceeded

                try:
                    Ledger(
                        Path(config["ledger"]),
                        config["budget_usd"],
                        config.get("max_requests", 128),
                    ).assert_available()
                except BudgetExceeded:
                    stop_reason = "budget_exhausted_before_trial"
                    break
            run_id = str(uuid.uuid4())
            out = output / run_id
            out.mkdir()
            req = {
                "root": str(root.resolve()),
                "task": task["id"],
                "config": config,
                "output_dir": str(out),
            }
            request = out / "request.json"
            response = out / "response.json"
            request.write_text(json.dumps(req, indent=2) + "\n")
            ledger_file = Path(config["ledger"]) if config.get("ledger") else None
            prior_requests = (
                {r["id"] for r in json.loads(ledger_file.read_text())["requests"]}
                if ledger_file and ledger_file.exists()
                else set()
            )
            prior_denials = (
                {
                    d["id"]
                    for d in json.loads(ledger_file.read_text()).get("denials", [])
                }
                if ledger_file and ledger_file.exists()
                else set()
            )
            started = datetime.now(timezone.utc)
            start = time.perf_counter()
            timeout = (
                config["timeout_s"]
                + config.get("setup_timeout_s", 60)
                + config.get("evaluation_timeout_s", 60)
                + 15
            )
            env = dict(os.environ, ANONYMIZED_TELEMETRY="false", PYTHONUNBUFFERED="1")
            snapshot = None
            environment_setup = 0.0
            worker_elapsed = 0.0
            r = {
                "status": "setup_error",
                "success": False,
                "score": 0.0,
                "error": "Trial interrupted",
            }
            with (out / "worker.log").open("w") as log:
                try:
                    if task["benchmark"] == "WebArena":
                        from .webarena_environment import Snapshot

                        snapshot = Snapshot(
                            config["webarena"],
                            task["verifier"]["configuration"]["sites"],
                            run_id,
                        )
                        try:
                            snapshot.start()
                        finally:
                            environment_setup = time.perf_counter() - start
                        req["config"] = dict(config, endpoints=snapshot.endpoints)
                        req["reset"] = snapshot.evidence
                        request.write_text(json.dumps(req, indent=2) + "\n")
                    code = run_isolated(
                        [
                            sys.executable,
                            "-m",
                            "agent_jit.worker",
                            str(request),
                            str(response),
                        ],
                        cwd=root,
                        env=env,
                        log=log,
                        timeout=timeout,
                    )
                    if code == 0 and response.exists():
                        r = json.loads(response.read_text())
                    else:
                        r = {
                            "status": "setup_error",
                            "success": False,
                            "score": 0.0,
                            "error": f"Worker exited {code} before returning a trial record; see worker.log",
                        }
                except (subprocess.TimeoutExpired, TimeoutError) as exc:
                    r = {
                        "status": "timeout",
                        "success": False,
                        "score": 0.0,
                        "error": f"Trial deadline exceeded: {exc}",
                    }
                except Exception as exc:
                    r = {
                        "status": "setup_error",
                        "success": False,
                        "score": 0.0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                finally:
                    worker_elapsed = time.perf_counter() - start - environment_setup
                    if snapshot is not None:
                        try:
                            snapshot.close()
                        except Exception as exc:
                            r = {
                                "status": "setup_error",
                                "success": False,
                                "score": 0.0,
                                "error": f"Environment cleanup failed: {exc}",
                            }
                        r["reset"] = {**r.get("reset", {}), **snapshot.evidence}
            wall = time.perf_counter() - start
            phase = (
                json.loads((out / "phase.json").read_text())
                if (out / "phase.json").exists()
                else {}
            )
            timing = r.get(
                "timing",
                {
                    "setup_s": phase.get("setup_s", 0.0),
                    "online_s": max(0.0, worker_elapsed - phase.get("setup_s", 0.0))
                    if phase.get("phase") == "agent"
                    else phase.get("online_s", 0.0),
                    "evaluation_s": 0.0,
                    "total_wall_s": wall,
                },
            )
            requests = (
                [
                    r
                    for r in json.loads(ledger_file.read_text())["requests"]
                    if r["id"] not in prior_requests
                ]
                if ledger_file and ledger_file.exists()
                else []
            )
            denials = (
                [
                    d
                    for d in json.loads(ledger_file.read_text()).get("denials", [])
                    if d["id"] not in prior_denials
                ]
                if ledger_file and ledger_file.exists()
                else []
            )
            if denials:
                r.update(
                    status="budget_exhausted",
                    success=False,
                    score=None,
                    error="Model request blocked by campaign budget/request limit; trial unscored and campaign stopped",
                )
            usage = {
                "budget_denials": denials,
                "ledger": config.get("ledger"),
                "request_ids": [r["id"] for r in requests],
                "estimated_cost_usd": sum(
                    r.get("estimated_cost_usd", r["reservation_usd"]) for r in requests
                )
                if ledger_file
                else None,
                "prompt_tokens": sum(
                    r.get("usage", {}).get("prompt_tokens", 0) for r in requests
                )
                if requests and all("usage" in r for r in requests)
                else None,
                "completion_tokens": sum(
                    r.get("usage", {}).get("completion_tokens", 0) for r in requests
                )
                if requests and all("usage" in r for r in requests)
                else None,
                "cost_basis": "Provider token counts at uncached list rates; unreconciled requests charged their full reservation. Null means unreported.",
            }
            timing["setup_s"] += environment_setup
            timing["total_wall_s"] = wall
            record = TrialResult(
                kind="fresh_run",
                run_id=run_id,
                task_id=task["id"],
                implementation_id=task["implementation_id"],
                benchmark=task["benchmark"],
                benchmark_revision=(
                    "WebArena dce04686a56253aefba7b18a4fa0937cf1dc987b + saved evaluator overrides; image IDs in reset"
                    if task["benchmark"] == "WebArena"
                    else task.get(
                        "benchmark_revision",
                        "REAL hosted URL; source/runtime revision unavailable",
                    )
                ),
                method=config["method"],
                method_revision=config["method_revision"],
                artifact_revision=revision(root),
                model=config.get("model"),
                cross_model_profile=cross_model_profile(config),
                trial=trial,
                status=r["status"],
                success=r["success"],
                score=r["score"],
                error=r.get("error"),
                started_at=started,
                timing=timing,
                configuration=config,
                resources={
                    "worker_budget": config.get("worker_budget", 1),
                    "host": platform.platform(),
                    "python": platform.python_version(),
                    "timeout_s": config["timeout_s"],
                },
                reset=r.get("reset", phase.get("reset", {})),
                evaluator=r.get("evaluator", {"mode": config["evaluator"]}),
                usage=usage,
                trace={
                    "directory": str(out.relative_to(output)),
                    "agent_metadata": r.get("agent_metadata", {}),
                    "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
                },
            )
            with result_path.open("a") as f:
                f.write(record.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            records.append(record)
            if record.status == "budget_exhausted":
                stop_reason = "budget_exhausted_during_trial"
                break
    summary = {
        "complete": stop_reason is None
        and len(records) == len(selected) * config["trials"],
        "result_file": str(result_path),
        "stop_reason": stop_reason,
        "requested_attempts": len(selected) * config["trials"],
        "not_started": len(selected) * config["trials"] - len(records),
        "attempts": len(records),
        "successes": sum(r.success for r in records),
        "accuracy": sum(r.success for r in records)
        / sum(r.status != "budget_exhausted" for r in records)
        if any(r.status != "budget_exhausted" for r in records)
        else None,
        "scored_attempts": sum(r.status != "budget_exhausted" for r in records),
        "statuses": [r.status for r in records],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def validate_results(path: Path):
    records = [
        TrialResult.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    if not records:
        raise ValueError("No trial records")
    if len({r.run_id for r in records}) != len(records):
        raise ValueError("Duplicate run IDs")
    expected = len(records[0].configuration.get("tasks", [])) * records[
        0
    ].configuration.get("trials", 1)
    return {
        "valid": True,
        "complete": len(records) == expected
        and not any(r.status == "budget_exhausted" for r in records),
        "attempts": len(records),
        "successes": sum(r.success for r in records),
        "accuracy": sum(r.success for r in records)
        / sum(r.status != "budget_exhausted" for r in records)
        if any(r.status != "budget_exhausted" for r in records)
        else None,
        "scored_attempts": sum(r.status != "budget_exhausted" for r in records),
    }
