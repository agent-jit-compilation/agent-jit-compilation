"""Original scheduler Monte Carlo model with explicit profiles, mode, and seed."""

import contextlib
import io
import hashlib
import json
import math
import random
from pathlib import Path
import numpy as np
from blast_scheduler.online.scheduler import SchedulerPlan, ParallelUsageCounts
from blast_scheduler.online.scheduler_cost_service import SchedulerCostService

# Named constants in the original cost service, made explicit instead of relying on
# its global Gemini-Pro defaults for every model.
OVERHEADS = {
    "gpt-4.1": (15.0, 30.0, 10.0, 7.0),
    "gemini-2.5-pro": (5.0, 20.0, 5.0, 5.0),
    "gemini-2.5-flash": (15.0, 30.0, 10.0, 7.0),
}


STRATEGIES = {"sequential": "serial", "parallel": "parallel", "first_of_n": "hedge"}


def resolve_inputs(
    root, application, model, profile_path=None, schema_root=None, overheads=None
):
    """Resolve caches and require explicit overheads for unprofiled model names."""
    if Path(application).name != application or application in (".", ".."):
        raise ValueError("application must be a plain directory name")
    profile = (
        Path(profile_path)
        if profile_path
        else root
        / f"data/scheduler/saved_sites/{application}/element_latencies_{model}.json"
    )
    schemas = (
        Path(schema_root) if schema_root else root / "data/scheduler/saved_schemas"
    )
    if not profile.is_file():
        raise ValueError(f"Latency profile does not exist: {profile}")
    if not (schemas / application).is_dir():
        raise ValueError(f"Page schemas do not exist: {schemas / application}")
    if overheads is None:
        if model not in OVERHEADS:
            raise ValueError(
                "Custom profile models require overheads_s (CLI: --overheads), with hedge, parallel, page_read, repeat_interaction in seconds"
            )
        values = OVERHEADS[model]
    else:
        keys = ("hedge", "parallel", "page_read", "repeat_interaction")
        if not isinstance(overheads, dict) or set(overheads) != set(keys):
            raise ValueError(
                "overheads must specify hedge, parallel, page_read, repeat_interaction"
            )
        values = tuple(overheads[k] for k in keys)
        if any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for v in values
        ):
            raise ValueError("Overheads must be finite nonnegative seconds")
    return profile, schemas, values


def estimate(
    root: Path,
    usage_path: Path,
    application: str,
    model: str,
    mode: str,
    seed: int,
    trials: int,
    *,
    profile_path: Path | None = None,
    schema_root: Path | None = None,
    overheads: dict | None = None,
):
    if not 1 <= trials <= 100000:
        raise ValueError("Monte Carlo trials must be in 1..100000")
    data = json.loads(usage_path.read_text())
    seq = SchedulerPlan.model_validate(data["sequential"])
    par = (
        ParallelUsageCounts.model_validate(data["parallel"])
        if data.get("parallel") is not None
        else None
    )
    if any(e.count < 0 or e.count > 10000 for e in seq.elements):
        raise ValueError("Element counts must be in 0..10000")
    if par and (
        not 1 <= par.num_workers <= 10000
        or any(
            not 0 <= e.count <= 10000
            for e in par.parallel_elements + par.rediscovery_elements
        )
    ):
        raise ValueError("Invalid parallel usage counts")
    if mode not in ("mean", "win_rate"):
        raise ValueError("mode must be mean or win_rate")
    profile, schema_root, costs = resolve_inputs(
        root, application, model, profile_path, schema_root, overheads
    )
    hedge, parallel, read, repeat = costs
    service = SchedulerCostService(
        element_latencies_file=str(profile),
        site_name=application,
        schema_root=str(schema_root),
        first_of_n_overhead=hedge,
        parallel_overhead=parallel,
        page_read_cost=read,
        repeat_interaction_cost=repeat,
    )
    np.random.seed(seed)
    random.seed(seed)
    # Preserve original algorithm (four concurrent workers for parallel batches and hedging).
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        selected = service.choose_best_strategy(
            seq, par, num_first_of_n_workers=4, num_trials=trials, mode=mode
        )
    inputs = []
    for path in [profile, *sorted((schema_root / application).glob("*.json"))]:
        resolved = path.resolve()
        label = (
            resolved.relative_to(root.resolve())
            if resolved.is_relative_to(root.resolve())
            else resolved
        )
        inputs.append(
            {
                "path": str(label),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return dict(
        kind="scheduler_estimate",
        schema_version="1.0",
        selected=STRATEGIES[selected],
        mode=mode,
        estimated_latency_s={
            STRATEGIES[k]: v for k, v in service.last_comparison["mean_times"].items()
        },
        win_rates={
            STRATEGIES[k]: v for k, v in service.last_comparison["win_rates"].items()
        },
        application=application,
        profile_model=model,
        seed=seed,
        monte_carlo_trials=trials,
        worker_budget=4,
        overheads_s={
            "hedge": hedge,
            "parallel": parallel,
            "page_read": read,
            "repeat_interaction": repeat,
        },
        inputs=inputs,
        profile_path=str(profile.resolve()),
        schema_root=str(schema_root.resolve()),
        usage_input=str(usage_path),
        scope="Monte Carlo estimate from supplied usage counts and offline profiles; no browser execution or model calls.",
        diagnostic=captured.getvalue().strip(),
    )
