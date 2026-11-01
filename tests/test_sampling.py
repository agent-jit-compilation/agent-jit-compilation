import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_jit.sampling import (
    CANDIDATE_TEMPERATURE,
    collect_candidates,
    validate_sampling,
    validate_temperature,
)


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


def test_candidates_are_sampled_at_the_paper_temperature():
    assert CANDIDATE_TEMPERATURE == 0.5
    assert validate_temperature(0) == 0.0
    for bad in (-0.1, 2.5, True, "0.5", None):
        with pytest.raises(ValueError, match="candidate_temperature"):
            validate_temperature(bad)


def test_generate_samples_candidates_at_the_configured_temperature(tmp_path, monkeypatch):
    pytest.importorskip("browser_use")
    import agent_jit.generate as generate_module
    from blastai.agents import codegen

    temperatures = []

    class Model:
        def __init__(self, model, ledger, tokens, temperature=0.0):
            temperatures.append(temperature)
            self.client = self

        async def close(self):
            pass

    class Generator:
        def __init__(self, *args, **kwargs):
            pass

        async def generate_candidate(self, **kwargs):
            return None

    monkeypatch.setattr(generate_module, "BudgetedModel", Model)
    monkeypatch.setattr(codegen, "CodeGenerator", Generator)
    registry = Path(__file__).resolve().parents[1] / "data/registries/dashdish.json"
    for name, kwargs in (("default", {}), ("greedy", {"candidate_temperature": 0})):
        asyncio.run(
            generate_module.generate(
                "fixture task", registry, {}, "gpt-4.1", 1, tmp_path / name,
                tmp_path / f"{name}.json", 1, 64, "sampled", None, 8, **kwargs,
            )
        )
    assert temperatures == [0.5, 0.0]


def test_collect_until_valid_stops_early_and_cancels_the_rest():
    import asyncio
    from types import SimpleNamespace
    from agent_jit.sampling import collect_until_valid

    # Launch order: slow valid, fast invalid, fast valid, medium valid, very slow valid.
    plan = [(0.30, True), (0.01, False), (0.02, True), (0.05, True), (5.0, True)]
    launched = iter(range(len(plan)))
    cancelled = []

    async def generate():
        i = next(launched)
        try:
            await asyncio.sleep(plan[i][0])
        except asyncio.CancelledError:
            cancelled.append(i)
            raise
        return SimpleNamespace(i=i, is_valid=plan[i][1])

    results, valid = asyncio.run(collect_until_valid(generate, len(plan), 2))
    assert [c.i for c in valid] == [2, 3]  # the first two valid arrivals, in launch order
    assert results[0] is None and results[4] is None and results[1].is_valid is False
    assert sorted(cancelled) == [0, 4]
