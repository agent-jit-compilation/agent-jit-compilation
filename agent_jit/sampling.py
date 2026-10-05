"""Bounded candidate collection with an explicit valid-plan target."""

import asyncio


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
