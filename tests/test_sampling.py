import asyncio
from types import SimpleNamespace

import pytest

from agent_jit.sampling import collect_candidates, validate_sampling


def test_valid_target_replaces_invalid_attempts_and_retains_failures():
    inputs = iter([False, True, ValueError("fixture"), True])

    async def attempt():
        value = next(inputs)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(is_valid=value)

    attempted, valid, met = asyncio.run(collect_candidates(attempt, 2, "valid", 4))
    assert met and len(attempted) == 4 and len(valid) == 2
    assert isinstance(attempted[2], ValueError)


def test_sampled_mode_and_attempt_exhaustion_are_distinct():
    async def invalid():
        return SimpleNamespace(is_valid=False)

    attempts, valid, met = asyncio.run(collect_candidates(invalid, 2, "sampled", 8))
    assert len(attempts) == 2 and not valid and met
    attempts, valid, met = asyncio.run(collect_candidates(invalid, 2, "valid", 4))
    assert len(attempts) == 4 and not valid and not met


def test_large_candidate_counts_are_accepted_but_invalid_limits_are_not():
    assert validate_sampling(64, "valid", 100) == 100
    with pytest.raises(ValueError):
        validate_sampling(64, "valid", 32)
