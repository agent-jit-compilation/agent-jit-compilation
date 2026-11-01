"""Bounded candidate collection with an explicit valid-plan target."""

import asyncio

# The paper sampled candidates at temperature 0.5 (experiments/evaluate_codegen.py). At 0.0,
# parallel samples are near-identical, so one systematic error invalidates every candidate.
CANDIDATE_TEMPERATURE = 0.5


def validate_temperature(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2:
        raise ValueError("candidate_temperature must be a number between 0 and 2")
    return float(value)


def validate_sampling(count, target, max_attempts):
    if type(count) is not int or count < 1:
        raise ValueError("candidates must be a positive integer")
    if target not in ("valid", "sampled"):
        raise ValueError("candidate_target must be valid or sampled")
    limit = (
        max_attempts
        if max_attempts is not None
        else (4 * count if target == "valid" else count)
    )
    if type(limit) is not int or limit < count:
        raise ValueError(
            "max_candidate_attempts must be an integer at least candidates"
        )
    return limit


async def collect_candidates(generate_one, count, target="valid", max_attempts=None):
    """Collect candidates in bounded batches; never silently lower a valid target.

    Each attempt uses the generator's own iteration limit. Candidate exceptions
    are retained for reporting. Budget and request limits apply to every attempt.
    """
    limit = validate_sampling(count, target, max_attempts)
    results = []
    valid = []
    while len(results) < limit:
        remaining = count - (len(valid) if target == "valid" else len(results))
        if remaining <= 0:
            break
        batch = min(remaining, limit - len(results), 32)
        returned = await asyncio.gather(
            *(generate_one() for _ in range(batch)), return_exceptions=True
        )
        results.extend(returned)
        valid.extend(
            c
            for c in returned
            if not isinstance(c, BaseException) and c is not None and c.is_valid
        )
    met = len(valid) >= count if target == "valid" else len(results) >= count
    return results, valid, met


async def collect_until_valid(generate_one, count, needed):
    """Launch count candidates at once; stop when needed valid ones have arrived.

    The rest are cancelled. Returns candidates in launch order (None for any that
    did not finish) and the valid ones in launch order, so ties still break by
    generation order.
    """
    tasks = [asyncio.ensure_future(generate_one()) for _ in range(count)]
    order = {task: i for i, task in enumerate(tasks)}
    results = [None] * count
    pending, arrived = set(tasks), 0
    try:
        while pending and arrived < needed:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    candidate = task.result()
                except Exception as error:  # retained for reporting, as in collect_candidates
                    candidate = error
                results[order[task]] = candidate
                arrived += not isinstance(candidate, BaseException) and candidate is not None and candidate.is_valid
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    valid = [c for c in results if not isinstance(c, BaseException) and c is not None and c.is_valid]
    return results, valid
