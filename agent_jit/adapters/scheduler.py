"""JIT-Scheduler or fixed strategy through the saved BLAST orchestration engine."""

import asyncio
from functools import partial
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from . import AgentOutput
from ..budget import Ledger
from ..browser_model import BrowserModelPool
from ..predict import predict
from ..history import save_history
from ..browser_state import read_final_state


class StateLogger:
    """Original state helpers pass an `indent` argument unsupported by logging.Logger."""

    def info(self, *args, **kwargs):
        pass

    warning = error = info


async def run(context):
    # Original telemetry is optional and must not be enabled by ambient credentials.
    import os

    os.environ.pop("LMNR_PROJECT_API_KEY", None)
    from blastai.engine import Engine
    from blastai.config import Settings, Constraints
    import blastai.cache as cache
    import blastai.planner as planner
    import blastai.resource_factory as factory
    import blastai.executor as executor_module
    from experiments.task_state_utils import (
        get_all_completed_tasks,
        get_task_for_evaluation,
        fetch_final_state,
        merge_parallel_final_states,
    )

    cfg = context.configuration
    action_limit = cfg.get("max_actions_per_step", 10)
    if type(action_limit) is not int or not 1 <= action_limit <= 10:
        raise ValueError("max_actions_per_step must be an integer in 1..10")
    if context.task["benchmark"] not in ("REAL", "custom"):
        raise ValueError("The paper's scheduling experiments use REAL only")
    if cfg.get("worker_budget", 4) != 4:
        raise ValueError("Recovered scheduler settings use four workers")
    selected = {"serial": "sequential", "hedge": "first_of_n"}.get(
        cfg.get("strategy", "jit"), cfg.get("strategy", "jit")
    )
    scheduling = 0.0
    if selected == "jit":
        prediction = await asyncio.to_thread(
            predict,
            context.root,
            context.task["task_details"],
            context.task["application"],
            cfg.get("profile_model", "gpt-4.1"),
            cfg.get("predictor_model", cfg["model"]),
            cfg["selection_mode"],
            cfg.get("seed", 0),
            cfg.get("monte_carlo_trials", 1000),
            Path(cfg["ledger"]),
            cfg["budget_usd"],
            context.output_dir / "schedule",
            profile_path=cfg.get("latency_profile"),
            schema_root=cfg.get("schema_root"),
            max_requests=cfg.get("max_requests", 128),
            overheads=cfg.get("overheads_s"),
        )
        selected = {"serial": "sequential", "hedge": "first_of_n"}.get(
            prediction["selected"], prediction["selected"]
        )
        scheduling = prediction["scheduling_s"]
    if selected not in ("sequential", "parallel", "first_of_n"):
        raise ValueError("strategy must be jit, sequential, parallel, or first_of_n")
    parallelism = {
        "task": selected == "parallel",
        "data": False,
        "first_of_n": selected == "first_of_n",
    }
    mini_model = cfg.get("mini_model", cfg["model"])
    models = BrowserModelPool(
        (cfg["model"], mini_model),
        Ledger(Path(cfg["ledger"]), cfg["budget_usd"], cfg.get("max_requests", 128)),
        cfg.get("max_output_tokens", 2048),
    )

    temporary = tempfile.TemporaryDirectory(prefix="agent-jit-engine-")
    profile_root = Path(temporary.name)

    def profile(task_id):
        path = profile_root / task_id
        path.mkdir(exist_ok=True)
        return str(path)

    # Per-trial process: replace only model creation and filesystem locations.
    # Task decomposition, worker allocation, racing, and state merging remain original.
    patches = [
        patch.object(module, "init_model", models.factory)
        for module in (cache, planner, factory)
    ]
    patches += [
        patch.object(cache, "get_appdata_dir", lambda: profile_root),
        patch.object(factory, "get_stealth_profile_dir", profile),
    ]
    if "max_actions_per_step" in cfg:
        patches.append(
            patch.object(
                executor_module,
                "Agent",
                partial(executor_module.Agent, max_actions_per_step=action_limit),
            )
        )
    for replacement in patches:
        replacement.start()
    engine = None

    async def cleanup():
        try:
            if engine is not None:
                try:
                    if cfg.get("save_browser_history", False):
                        for task_id, task in engine.scheduler.tasks.items():
                            executor = task.executor
                            agent = executor.agent if executor is not None else None
                            history = (
                                agent.history if agent is not None else task.result
                            )
                            save_history(history, context.output_dir, task_id)
                finally:
                    await engine.stop()
        finally:
            try:
                await models.close()
            finally:
                for replacement in reversed(patches):
                    replacement.stop()
                temporary.cleanup()

    try:
        from patchright.async_api import async_playwright

        async with async_playwright() as pw:
            browser_path = cfg.get("browser_executable", pw.chromium.executable_path)
        settings = Settings(
            local_browser_path=browser_path,
            persist_cache=False,
            secrets_file_path=None,
            logs_dir=str(context.output_dir / "engine"),
        )
        constraints = Constraints(
            llm_model=cfg["model"],
            llm_model_mini=mini_model,
            allow_parallelism=parallelism,
            first_of_n_num_copies=4,
            max_parallel_workers=4,
            max_concurrent_browsers=5,
            max_parallelism_nesting_depth=1,
            allow_vision=cfg.get("allow_vision", True),
            require_headless=cfg.get("headless", True),
            require_patchright=True,
            require_human_in_loop=False,
        )
        engine = Engine(settings=settings, constraints=constraints)
        execution_start = time.perf_counter()
        await engine.start()
        result = await engine.run(
            context.task["task_details"],
            initial_url=context.task["initial_url"],
            mode="block",
        )
        execution_s = time.perf_counter() - execution_start
        metrics = await engine.get_metrics()
        (context.output_dir / "engine-metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str) + "\n"
        )

        async def collect_state():
            states = engine.scheduler.tasks
            logger = StateLogger()

            async def fetch(task):
                if context.task["benchmark"] != "custom":
                    return await fetch_final_state(
                        task, context.task["initial_url"], logger
                    )
                page = await task.executor.browser_session.get_current_page()
                if page is None:
                    raise RuntimeError("No active worker tab for state evaluation")
                return await read_final_state(page, context.task)

            if selected == "parallel":
                completed = get_all_completed_tasks(states, logger)
                values = await asyncio.gather(*(fetch(t) for t in completed))
                # Original runner discards missing states, retained for fidelity and disclosed.
                valid = {t.id: v for t, v in zip(completed, values) if v is not None}
                (context.output_dir / "worker-final-states.json").write_text(
                    json.dumps(valid, indent=2) + "\n"
                )
                if context.task["benchmark"] == "custom":
                    # Only the custom verifier knows how to combine its state.
                    return {"workers": valid}
                return merge_parallel_final_states(valid, logger)
            winner = get_task_for_evaluation(parallelism, states, logger)
            if winner is None:
                raise RuntimeError(
                    "No completed worker is available for state evaluation"
                )
            return await fetch(winner)

        return AgentOutput(
            text=result.final_result() or "",
            planning_s=scheduling,
            execution_s=execution_s,
            metadata={
                "selected_strategy": {
                    "sequential": "serial",
                    "first_of_n": "hedge",
                }.get(selected, selected),
                "reported_success": bool(result.is_successful()),
                "worker_budget": 4,
                "max_browser_contexts_including_coordinator": 5,
                "idle_harness_browser_contexts": 1,
                "primary_model": cfg["model"],
                "mini_model": mini_model,
                "max_actions_per_step": cfg.get("max_actions_per_step", 10),
            },
            state_provider=collect_state,
            cleanup=cleanup,
        )
    except BaseException:
        await cleanup()
        raise
