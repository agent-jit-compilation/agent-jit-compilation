from pathlib import Path
import json
import shutil
import pytest
from agent_jit.scheduler import estimate

ROOT = Path(__file__).resolve().parents[1]


def test_profile_and_seed_control_estimates():
    args = (
        ROOT,
        ROOT / "examples/scheduler-usage.json",
        "dashdish",
        "gpt-4.1",
        "mean",
        123,
        20,
    )
    first = estimate(*args)
    second = estimate(*args)
    assert first["estimated_latency_s"] == second["estimated_latency_s"]
    assert first["overheads_s"]["hedge"] == 15
    assert first["selected"] == min(
        first["estimated_latency_s"], key=first["estimated_latency_s"].get
    )
    assert sum(first["win_rates"].values()) == pytest.approx(1)
    assert first["worker_budget"] == 4


def test_empty_monte_carlo_run_rejected():
    with pytest.raises(ValueError):
        estimate(
            ROOT,
            ROOT / "examples/scheduler-usage.json",
            "dashdish",
            "gpt-4.1",
            "mean",
            0,
            0,
        )


def test_custom_cache_paths_affect_costs_and_are_fingerprinted(tmp_path):
    schemas = tmp_path / "schemas"
    shutil.copytree(
        ROOT / "data/scheduler/saved_schemas/dashdish", schemas / "dashdish"
    )
    data = json.loads(
        (
            ROOT / "data/scheduler/saved_sites/dashdish/element_latencies_gpt-4.1.json"
        ).read_text()
    )
    for element in data["element_latencies"].values():
        element["distribution"]["name"] = "constant"
        element["distribution"]["params"] = [1000.0]
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(data))
    args = (
        ROOT,
        ROOT / "examples/scheduler-usage.json",
        "dashdish",
        "gpt-4.1",
        "mean",
        0,
        10,
    )
    normal = estimate(*args)
    custom = estimate(*args, profile_path=profile, schema_root=schemas)
    assert custom["estimated_latency_s"] != normal["estimated_latency_s"]
    assert custom["profile_path"] == str(profile)
    assert custom["schema_root"] == str(schemas)
    assert any(item["path"] == str(profile) for item in custom["inputs"])


def test_custom_application_and_model_require_explicit_costs(tmp_path):
    schemas = tmp_path / "schemas"
    shutil.copytree(
        ROOT / "data/scheduler/saved_schemas/dashdish", schemas / "custom-shop"
    )
    profile = (
        ROOT / "data/scheduler/saved_sites/dashdish/element_latencies_gpt-4.1.json"
    )
    args = (
        ROOT,
        ROOT / "examples/scheduler-usage.json",
        "custom-shop",
        "new-model",
        "mean",
        0,
        10,
    )
    with pytest.raises(ValueError, match="overheads"):
        estimate(*args, profile_path=profile, schema_root=schemas)
    result = estimate(
        *args,
        profile_path=profile,
        schema_root=schemas,
        overheads={"hedge": 1, "parallel": 2, "page_read": 3, "repeat_interaction": 4},
    )
    assert result["profile_model"] == "new-model"
    assert set(result["estimated_latency_s"]) == {"serial", "parallel", "hedge"}
    assert result["overheads_s"]["parallel"] == 2
