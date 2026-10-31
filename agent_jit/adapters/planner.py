"""JIT-Planner candidate evaluation + original SMCP executor; no serving stack.

Uses the paper's sampled-candidate selection, not AgentExecutor.run's later
hardcoded multi-model production policy. Initial navigation is included online.
"""

import ast
from dataclasses import asdict
import json
from pathlib import Path
import time
from pydantic import BaseModel
from . import AgentOutput
from .registry import load_agent
from ..budget import Ledger, BudgetedModel
from ..sampling import (
    CANDIDATE_TEMPERATURE,
    collect_candidates,
    validate_sampling,
    validate_temperature,
)
from ..browser_state import state_provider


def answer_text(value):
    """Preserve text answers and serialize structured results as JSON."""

    def structured(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, (set, frozenset)):
            return sorted(item, key=str)
        raise TypeError(f"Cannot serialize planner result: {type(item).__name__}")

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=structured)


def called_names(code):
    tree = compile(code, "<plan>", "exec", ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def reject_incomplete(candidate, limit, tools):
    """Invalidate a parseable candidate that cannot complete the task.

    Best-of-n selection runs the cheapest valid plan. A plan cut off at the output-token
    limit, or one that never calls an application tool, does the least and so ranks
    cheapest; selection would otherwise favor it.
    """
    if not candidate or not candidate.is_valid:
        return candidate
    if any((timing.total_tokens or 0) >= limit for timing in candidate.llm_timings):
        candidate.is_valid = False
        candidate.validation_error = f"Output reached the {limit}-token limit"
    elif not tools & called_names(candidate.code):
        candidate.is_valid = False
        candidate.validation_error = "Plan calls no application tool"
    return candidate


def recorded_candidates(directory):
    """Candidates and planning time recorded by an earlier trial of this adapter."""
    from blastai.agents.codegen import CodeCandidate, LLMTiming

    candidates = [
        CodeCandidate(**{**c, "llm_timings": [LLMTiming(**t) for t in c["llm_timings"]]})
        if c and "code" in c
        else None
        for c in json.loads((directory / "candidates.json").read_text())
    ]
    planning = json.loads((directory / "response.json").read_text())["timing"]["planning_s"]
    if planning is None:
        # A trial that failed during execution records no planning time. Candidates are
        # generated in parallel, so planning lasted at least as long as the slowest one.
        planning = max(c.total_time for c in candidates if c)
    return candidates, planning


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
    temperature = validate_temperature(
        cfg.get("candidate_temperature", CANDIDATE_TEMPERATURE)
    )
    agent = load_agent(context)
    tools = {tool.name for tool in agent.tools}
    ledger = Ledger(
        Path(cfg["ledger"]), cfg["budget_usd"], cfg.get("max_requests", 128)
    )
    tokens = cfg.get("max_output_tokens", 1024)
    # Candidates are sampled as in the paper; inference inside the executed plan stays at 0.
    llm = BudgetedModel(cfg["model"], ledger, tokens)
    generation_llm = BudgetedModel(cfg["model"], ledger, tokens, temperature)
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
            try:
                await llm.client.close()
            finally:
                await generation_llm.client.close()

    start = time.perf_counter()
    try:
        await executor._ensure_browser_started()
        executor.python_executor = executor._create_python_executor()
        await executor.python_executor.state["goto"](context.task["initial_url"])
        state = executor.python_executor.state.get("STATE", {})
        if not state:
            raise RuntimeError("Initial abstract state was not observed")
        generator = CodeGenerator(
            agent, llm=generation_llm, state_aware=True, max_iterations=1
        )

        async def generate():
            return reject_incomplete(
                await generator.generate_candidate(
                    task=context.task["task_details"],
                    history=[],
                    initial_state=state,
                    current_url=context.task["initial_url"],
                ),
                tokens,
                tools,
            )

        replay = cfg.get("replay_candidates", {}).get(context.task["id"])
        if replay:
            # Re-execute an earlier trial's candidates without generating new ones.
            results, recorded_planning = recorded_candidates(Path(replay))
            results = [reject_incomplete(c, tokens, tools) for c in results]
            valid = [c for c in results if c and c.is_valid]
            target_met = True
        else:
            results, valid, target_met = await collect_candidates(generate, k, target, limit)
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
            # The paper's planning time: the selected plan's own generation, not the wait for all candidates.
            "selected_generation_s": best.total_time,
        }
        (context.output_dir / "selection.json").write_text(
            json.dumps(selection_metadata, indent=2) + "\n"
        )
        planning = recorded_planning if replay else time.perf_counter() - start
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
                "candidate_temperature": temperature,
                "requested_candidates": k,
                "valid_candidates": len(valid),
                "estimated_cost": best.rank,
                "selection": selection,
                **({"replayed_from": Path(replay).name} if replay else {}),
                **selection_metadata,
            },
            state_provider=state_provider(browser, context.task),
            cleanup=cleanup,
        )
    except BaseException:
        await cleanup()
        raise
