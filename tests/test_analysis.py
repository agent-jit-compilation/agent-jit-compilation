from pathlib import Path
import pytest
from agent_jit.analysis import figure5, figure8, figure10, pass_at_k

ROOT = Path(__file__).resolve().parents[1]


def test_protocol_coverage_and_recorded_counts():
    result = figure5(ROOT)
    assert len(result["points"]) == 14
    gpt = next(p for p in result["points"] if p["model"] == "gpt-4.1" and p["protocol"])
    assert (gpt["n"], gpt["valid"]) == (864, 776)
    assert gpt["latency_s"] == pytest.approx(7.35726252336193)
    assert len(result["inputs"]) == 169


def test_pass_at_k_and_case_study():
    assert pass_at_k(32, 1, 3) == pytest.approx(3 / 32)
    assert pass_at_k(32, 32, 3) == 1
    with pytest.raises(ValueError):
        pass_at_k(32, 1, 33)
    result = figure8(ROOT)
    pro = [p for p in result["series"] if p["model"] == "gemini-2.5-pro"]
    assert [(p["protocol"], p["valid"]) for p in pro] == [(True, 32), (False, 1)]
    assert pro[1]["k"][2][1] == pytest.approx(3 / 32)


def test_scheduler_retains_failures_and_reports_paper_differences():
    result = figure10(ROOT)
    gpt = next(
        p
        for p in result["points"]
        if p["model"] == "GPT-4.1" and p["strategy"] == "Serial"
    )
    assert gpt["tasks"] == 27 and gpt["effective_trials"] == 81
    assert gpt["accuracy"] == pytest.approx(47 / 81)
    jit = next(
        p
        for p in result["points"]
        if p["model"] == "GPT-4.1" and p["strategy"] == "JIT-Scheduler"
    )
    assert jit["latency_s"] == pytest.approx(142.60965, abs=0.001)
    assert result["parallel_normalization"] == []
    assert len(result["differences_from_paper"]) == 17
    assert all(p["source_kind"] != "paper_reference_constant" for p in result["points"])
    parallel = next(
        p
        for p in result["points"]
        if p["model"] == "GPT-4.1" and p["strategy"] == "Parallel"
    )
    assert parallel["latency_s"] == pytest.approx(166.232246, abs=0.000001)
    assert parallel["accuracy"] == pytest.approx(55 / 81)
    cua = next(p for p in result["points"] if p["model"] == "OpenAI CUA")
    assert cua["effective_trials"] == 81
    assert cua["accuracy"] == pytest.approx(63 / 81)
    assert cua["latency_s"] == pytest.approx(258.7262962963)
    assert (
        max(abs(d["latency_delta_s"]) for d in result["differences_from_paper"]) < 0.063
    )
    assert (
        max(abs(d["accuracy_delta"]) for d in result["differences_from_paper"])
        < 0.001532
    )


def test_parallel_normalization_is_explicit_sensitivity_analysis():
    result = figure10(ROOT, normalize_non_parallel=True)
    assert result["parallel_normalization"]
    pro = next(
        p
        for p in result["points"]
        if p["model"] == "Gemini-2.5-Pro" and p["strategy"] == "JIT-Scheduler"
    )
    assert pro["latency_s"] == pytest.approx(102.057977, abs=0.000001)
