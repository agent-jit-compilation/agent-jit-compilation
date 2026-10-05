"""One isolated benchmark trial. Invoked by the runner in its own process group."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from .adapters import RunContext, AgentOutput
from .catalog import task as get_task
from .evaluation import evaluate
from .process import await_with_deadline
from .browser_state import read_final_state


async def run(request):
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    config = request["config"]
    root = Path(request["root"])
    out = Path(request["output_dir"])
    task = (
        get_task(root, request["task"], config["task_catalog"])
        if config.get("task_catalog")
        else get_task(root, request["task"])
    )
    from patchright.async_api import async_playwright

    start = time.perf_counter()
    setup = online = eval_time = 0.0
    phase = "setup"
    reset = dict(request.get("reset", {}))
    agent_output = None
    result = {
        "status": "setup_error",
        "success": False,
        "score": 0.0,
        "error": None,
        "reset": reset,
        "evaluator": {"mode": config["evaluator"]},
    }

    async def checkpoint():
        (out / "phase.json").write_text(
            json.dumps(
                {
                    "phase": phase,
                    "elapsed_s": time.perf_counter() - start,
                    "setup_s": setup,
                    "online_s": online,
                    "reset": reset,
                }
            )
        )

    try:
        await checkpoint()
        with tempfile.TemporaryDirectory(prefix="agent-jit-browser-") as profile:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            async with async_playwright() as pw:
                options = {
                    "headless": config.get("headless", True),
                    "args": [f"--remote-debugging-port={port}"],
                    "viewport": {"width": 1280, "height": 720},
                }
                if config.get("browser_executable"):
                    options["executable_path"] = config["browser_executable"]
                browser = await pw.chromium.launch_persistent_context(
                    profile, **options
                )
                try:
                    page = (
                        browser.pages[0] if browser.pages else await browser.new_page()
                    )
                    url = config.get("endpoints", {}).get(
                        task["application"], task["initial_url"]
                    )
                    task["initial_url"] = url
                    if task["benchmark"] == "WebArena":
                        from .webarena import resolve_config
                        from .webarena_environment import login

                        wa_config = resolve_config(task, config["endpoints"])
                        (out / "webarena-task.json").write_text(
                            json.dumps(wa_config, indent=2) + "\n"
                        )
                        reset["authentication"] = await login(
                            page,
                            wa_config["sites"],
                            config["endpoints"],
                            root=root,
                            checkout=config["webarena"]["checkout"],
                            auth_folder=Path(profile) / ".auth",
                            log_path=out / "auto-login.log",
                            timeout=config.get("setup_timeout_s", 60),
                        )
                        url = wa_config["start_url"]
                        task["initial_url"] = url
                    await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=config.get("setup_timeout_s", 60) * 1000,
                    )
                    reset.update(
                        {
                            "method": reset.get(
                                "method", "fresh_temporary_browser_profile"
                            ),
                            "new_profile": True,
                            "initial_url": url,
                            "initial_storage_entries": await page.evaluate(
                                "() => Object.keys(localStorage).length"
                            ),
                            "server_reset": reset.get(
                                "server_reset", "not_requested_client_state_benchmark"
                            ),
                        }
                    )
                    setup = time.perf_counter() - start
                    phase = "agent"
                    await checkpoint()
                    module, name = config["adapter"].split(":", 1)
                    adapter = getattr(importlib.import_module(module), name)
                    context = RunContext(
                        task, page, f"http://127.0.0.1:{port}", root, out, config
                    )
                    t = time.perf_counter()
                    try:
                        agent_output = await await_with_deadline(
                            adapter(context), config["timeout_s"]
                        )
                    finally:
                        online = time.perf_counter() - t
                    if not isinstance(agent_output, AgentOutput):
                        raise TypeError("Adapter must return AgentOutput")
                    (out / "answer.txt").write_text(agent_output.text)
                    phase = "evaluation"
                    await checkpoint()
                    t = time.perf_counter()
                    try:
                        final_state = None
                        if task["verifier"]["type"] == "state_check" or task[
                            "verifier"
                        ].get("state_url"):
                            if agent_output.state_provider is not None:
                                final_state = await asyncio.wait_for(
                                    agent_output.state_provider(),
                                    config.get("evaluation_timeout_s", 60),
                                )
                                (out / "final-state.json").write_text(
                                    json.dumps(final_state, indent=2) + "\n"
                                )
                            else:
                                final_state = await asyncio.wait_for(
                                    read_final_state(page, task),
                                    config.get("evaluation_timeout_s", 60),
                                )
                                (out / "final-state.json").write_text(
                                    json.dumps(final_state, indent=2) + "\n"
                                )
                        if task["benchmark"] == "WebArena":
                            ev = await native_webarena_evaluation(
                                config,
                                out,
                                agent_output.text,
                                f"http://127.0.0.1:{port}",
                            )
                        else:
                            ev = await asyncio.wait_for(
                                evaluate(
                                    task,
                                    agent_output.text,
                                    final_state,
                                    config["evaluator"],
                                    Path(config["ledger"]).resolve()
                                    if config.get("ledger")
                                    else None,
                                    config.get("budget_usd", 1),
                                    max_requests=config.get("max_requests", 128),
                                    parser_model=config.get(
                                        "parser_model", "gpt-4.1-2025-04-14"
                                    ),
                                ),
                                timeout=config.get("evaluation_timeout_s", 60),
                            )
                    finally:
                        eval_time = time.perf_counter() - t
                    result.update(
                        status="success" if ev["correct"] else "failure",
                        success=ev["correct"],
                        score=ev["score"],
                        evaluator=ev,
                    )
                finally:
                    try:
                        if (
                            agent_output is not None
                            and agent_output.cleanup is not None
                        ):
                            await agent_output.cleanup()
                    finally:
                        await browser.close()
    except asyncio.TimeoutError:
        result.update(
            status="timeout", success=False, score=0.0, error=f"Timeout during {phase}"
        )
    except Exception as exc:
        # Error class and message are useful for local diagnosis; inspect traces before sharing.
        result.update(
            status={
                "setup": "setup_error",
                "agent": "agent_error",
                "evaluation": "evaluation_error",
            }[phase],
            success=False,
            score=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )
    if phase == "setup":
        setup = time.perf_counter() - start
    result["agent_metadata"] = agent_output.metadata if agent_output else {}
    result["timing"] = {
        "setup_s": setup,
        "online_s": online,
        "evaluation_s": eval_time,
        "total_wall_s": time.perf_counter() - start,
        "planning_s": agent_output.planning_s if agent_output else None,
        "execution_s": agent_output.execution_s if agent_output else None,
    }
    return result


async def native_webarena_evaluation(config, out, text, cdp_url):
    request = out / "webarena-evaluation-request.json"
    response = out / "webarena-evaluation.json"
    request.write_text(
        json.dumps(
            {
                "checkout": config["webarena"]["checkout"],
                "endpoints": config["endpoints"],
                "config_file": str(out / "webarena-task.json"),
                "text": text,
                "cdp_url": cdp_url,
                "llm_judge": config["webarena"].get("llm_judge", False),
                "ledger_path": config.get("ledger"),
                "budget": config.get("budget_usd", 1),
                "max_requests": config.get("max_requests", 128),
            }
        )
    )
    env = dict(os.environ)
    if config["webarena"].get("nltk_data"):
        env["NLTK_DATA"] = config["webarena"]["nltk_data"]
    # A separate synchronous Playwright process can inspect the live browser while
    # this async owner continues servicing its connection. No cached DOM surrogate.
    with (out / "webarena-evaluator.log").open("w") as log:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_jit.webarena",
            str(request),
            str(response),
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            code = await asyncio.wait_for(
                proc.wait(), config.get("evaluation_timeout_s", 60)
            )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    if code or not response.exists():
        raise RuntimeError(
            "Native WebArena evaluator failed; see webarena-evaluator.log"
        )
    return json.loads(response.read_text())


if __name__ == "__main__":
    req = json.loads(Path(sys.argv[1]).read_text())
    response = asyncio.run(run(req))
    Path(sys.argv[2]).write_text(json.dumps(response, indent=2, allow_nan=False) + "\n")
