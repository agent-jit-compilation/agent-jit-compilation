"""Check that profile rebuilding retains the original per-trace aggregation."""

import json

import pytest

from agent_jit.profiles import build_profile
from blast_scheduler.offline.element_latencies import LatencyDistributionBuilder


def test_profile_aggregates_steps_within_traces_and_rejects_missing_data(tmp_path):
    site = tmp_path / "site.json"
    site.write_text(
        json.dumps(
            {
                "site_name": "fixture",
                "element_mappings": {"Submit": {"trace1": [0, 1, 2], "trace2": [0]}},
            }
        )
    )
    steps = tmp_path / "steps"
    steps.mkdir()
    (steps / "trace1.json").write_text(
        json.dumps([{"total_latency": 0}, {"total_latency": 2}, {"total_latency": 3}])
    )
    with pytest.raises(FileNotFoundError):
        build_profile(site, steps, tmp_path / "missing", "fixture-model")
    assert not (tmp_path / "missing").exists()
    (steps / "trace2.json").write_text(json.dumps([{"total_latency": 7}]))
    report = build_profile(site, steps, tmp_path / "profile", "fixture-model")
    profile = LatencyDistributionBuilder.load_element_latencies(
        str(tmp_path / "profile/element_latencies.json")
    )
    assert profile["Submit"].values == [5, 7]
    assert profile["Submit"].distribution.name == "constant"
    assert profile["Submit"].distribution.params == [5]
    assert report["model_calls"] == 0 and report["observations"] == {"Submit": 2}
    (steps / "trace2.json").write_text(json.dumps([{"total_latency": -1}]))
    with pytest.raises(ValueError, match="nonnegative latency"):
        build_profile(site, steps, tmp_path / "negative", "fixture-model")
