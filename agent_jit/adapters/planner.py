"""JIT-Planner candidate evaluation + original SMCP executor; no serving stack.

Uses the paper's sampled-candidate selection, not AgentExecutor.run's later
hardcoded multi-model production policy. Initial navigation is included online.
"""

from dataclasses import asdict
import json
from pathlib import Path
import time
from pydantic import BaseModel
from . import AgentOutput
from .registry import load_agent
from ..budget import Ledger, BudgetedModel
from ..sampling import collect_candidates, validate_sampling
from ..browser_state import state_provider


def answer_text(value):
    """Preserve text answers and serialize structured results as JSON."""

    def structured(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"Cannot serialize planner result: {type(item).__name__}")

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=structured)


def select_candidate(candidates, selection):
    if selection not in ("best", "worst"):
        raise ValueError("selection must be best or worst")
    valid = [candidate for candidate in candidates if candidate and candidate.is_valid]
    if not valid:
        raise RuntimeError("No valid plan among sampled candidates")
    choose = min if selection == "best" else max
    return choose(valid, key=lambda candidate: candidate.rank)


async def run(context):
    from browser_use import Browser
    from blastai.agents.codegen import CodeGenerator
    from blastai.agents.executor import AgentExecutor

    cfg = context.configuration
    if not cfg.get("ledger") or not cfg.get("budget_usd"):
        raise ValueError("Planner requires ledger and budget_usd")
    selection = cfg.get("selection", "best")
    if selection not in ("best", "worst"):
        raise ValueError("selection must be best or worst")
    k = cfg.get("candidates", 1)
    target = cfg.get("candidate_target", "valid")
    limit = cfg.get("max_candidate_attempts")
    validate_sampling(k, target, limit)
    agent = load_agent(context)
    llm = BudgetedModel(
        cfg["model"],
        Ledger(Path(cfg["ledger"]), cfg["budget_usd"], cfg.get("max_requests", 128)),
        cfg.get("max_output_tokens", 1024),
    )
    browser = Browser(cdp_url=context.cdp_url, keep_alive=True)
    executor = AgentExecutor(
        agent=agent,
        llm=llm,
        codegen_llm=llm,
        browser=browser,
        state_aware=True,
        stop_if_codegen_fails=True,
        disable_ai_exec_fallback=True,
    )

    async def cleanup():
        try:
            await executor.cleanup()
        finally:
            await llm.client.close()

    start = time.perf_counter()
    try:
        await executor._ensure_browser_started()
        executor.python_executor = executor._create_python_executor()
        await executor.python_executor.state["goto"](context.task["initial_url"])
        state = executor.python_executor.state.get("STATE", {})
        if not state:
            raise RuntimeError("Initial abstract state was not observed")
        generator = CodeGenerator(agent, llm=llm, state_aware=True, max_iterations=1)
        results, valid, target_met = await collect_candidates(
            lambda: generator.generate_candidate(
                task=context.task["task_details"],
                history=[],
                initial_state=state,
                current_url=context.task["initial_url"],
            ),
            k,
            target,
            limit,
        )
        (context.output_dir / "candidates.json").write_text(
            json.dumps(
                [
                    {"is_valid": False, "error_type": type(c).__name__}
                    if isinstance(c, BaseException)
                    else asdict(c)
                    if c
                    else None
                    for c in results
                ],
                indent=2,
            )
            + "\n"
        )
        if not target_met:
            raise RuntimeError(
                f"Candidate target not reached: {len(valid)} valid plans from {len(results)} attempts; required {k} {target} candidates"
            )
        best = select_candidate(valid, selection)
        (context.output_dir / "selected.py").write_text(best.code + "\n")
        selection_metadata = {
            "selected_candidate": next(
                i for i, candidate in enumerate(results) if candidate is best
            ),
            "tied_candidates": [
                i
                for i, candidate in enumerate(results)
                if not isinstance(candidate, BaseException)
                and candidate is not None
                and candidate.is_valid
                and candidate.rank == best.rank
            ],
            "estimated_cost": best.rank,
            "tie_break": "first candidate in generation order",
        }
        (context.output_dir / "selection.json").write_text(
            json.dumps(selection_metadata, indent=2) + "\n"
        )
        planning = time.perf_counter() - start
        start_execution = time.perf_counter()
        result = await executor.python_executor(best.code)
        execution = time.perf_counter() - start_execution
        if result.error:
            raise RuntimeError(result.error)
        return AgentOutput(
            answer_text(result.output),
            planning,
            execution,
            {
                "candidate_count": len(results),
                "candidate_target": target,
                "requested_candidates": k,
                "valid_candidates": len(valid),
                "estimated_cost": best.rank,
                "selection": selection,
                **selection_metadata,
            },
            state_provider=state_provider(browser, context.task),
            cleanup=cleanup,
        )
    except BaseException:
        await cleanup()
        raise
