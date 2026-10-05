"""Bounded invocation of the original CodeGenerator and its validation/cost path."""

from dataclasses import asdict
import json
import os
import time
from .budget import Ledger, BudgetedModel
from .sampling import collect_candidates, validate_sampling


async def generate(
    task,
    registry,
    state,
    model,
    candidates,
    output,
    ledger_path,
    budget,
    max_output_tokens,
    candidate_target="valid",
    max_candidate_attempts=None,
    max_requests=128,
):
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    from blastai.agents.models import Agent
    from blastai.agents.codegen import CodeGenerator

    validate_sampling(candidates, candidate_target, max_candidate_attempts)
    if not isinstance(state, dict):
        raise ValueError("Initial state must be a JSON object")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "generation.json").exists():
        raise ValueError(
            "Output already contains generation.json; choose a new run directory"
        )
    ledger = Ledger(ledger_path, budget, max_requests=max_requests)
    llm = BudgetedModel(model, ledger, max_output_tokens)
    generator = CodeGenerator(
        Agent.from_smcp_registry(str(registry)),
        llm=llm,
        state_aware=True,
        num_candidates=1,
        max_iterations=1,
    )
    start = time.perf_counter()
    try:
        results, _, target_met = await collect_candidates(
            lambda: generator.generate_candidate(
                task=task, history=[], initial_state=state
            ),
            candidates,
            candidate_target,
            max_candidate_attempts,
        )
    finally:
        await llm.client.close()
    duration = time.perf_counter() - start
    records = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            records.append(
                {"candidate": i, "is_valid": False, "error_type": type(r).__name__}
            )
            continue
        if r is None:
            records.append(
                {"candidate": i, "is_valid": False, "error_type": "NoCandidateReturned"}
            )
            continue
        record = asdict(r)
        record["candidate"] = i
        records.append(record)
        if r.code:
            (output / f"candidate-{i}.py").write_text(r.code + "\n")
    valid = [r for r in records if r["is_valid"]]
    best = min(valid, key=lambda r: r["rank"]) if valid and target_met else None
    if best:
        (output / "selected.py").write_text(best["code"] + "\n")
    result = dict(
        schema_version="1.0",
        kind="live_plan_generation",
        task=task,
        model=model,
        registry=str(registry),
        initial_state=state,
        num_candidates=candidates,
        max_iterations=1,
        candidate_target=candidate_target,
        target_met=target_met,
        attempts=len(records),
        planning_seconds=duration,
        selected_candidate=best["candidate"] if best else None,
        candidates=records,
        tied_candidates=[
            r["candidate"] for r in valid if best and r["rank"] == best["rank"]
        ],
        tie_break="first candidate in generation order",
        budget_ledger=str(ledger_path),
        scope="Fresh API plan generation, static validation and ranking only; no benchmark execution.",
    )
    (output / "generation.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    return {
        "output": str(output.resolve()),
        "valid_candidates": len(valid),
        "candidates": len(records),
        "selected_candidate": result["selected_candidate"],
        "planning_seconds": duration,
        "budget_ledger": str(ledger_path),
    }
