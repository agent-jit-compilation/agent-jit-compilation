from datetime import datetime, timezone
import json
import pytest
from pydantic import ValidationError
from agent_jit.results import TrialResult
from agent_jit.runner import validate_results


def record(status="success", run_id="trial-1"):
    return dict(
        schema_version="1.0",
        kind="mock_test",
        run_id=run_id,
        task_id="fixture",
        implementation_id="fixture",
        benchmark="mock",
        benchmark_revision="fixture-v1",
        method="fixture-agent",
        method_revision="v1",
        artifact_revision="fixture",
        model=None,
        trial=0,
        status=status,
        success=status == "success",
        score=1.0 if status == "success" else 0.0,
        started_at=datetime.now(timezone.utc).isoformat(),
        timing={
            "setup_s": 0.0,
            "online_s": 1.0,
            "evaluation_s": 0.0,
            "total_wall_s": 1.0,
        },
        configuration={},
        resources={},
        reset={"method": "fixture"},
        evaluator={"mode": "fixture"},
        usage={},
        trace={},
    )


def test_failures_timeouts_and_setup_errors_stay_in_denominator(tmp_path):
    statuses = [
        "success",
        "failure",
        "timeout",
        "setup_error",
        "agent_error",
        "evaluation_error",
    ]
    p = tmp_path / "results.jsonl"
    p.write_text(
        "\n".join(json.dumps(record(s, str(i))) for i, s in enumerate(statuses))
    )
    report = validate_results(p)
    assert (
        report["attempts"] == 6
        and report["successes"] == 1
        and report["accuracy"] == pytest.approx(1 / 6)
    )


def test_no_successful_timeout_or_mock_benchmark_mislabel():
    d = record()
    d["status"] = "timeout"
    with pytest.raises(ValidationError):
        TrialResult.model_validate(d)
    d = record()
    d["benchmark"] = "REAL"
    with pytest.raises(ValidationError):
        TrialResult.model_validate(d)


def test_verifier_success_with_partial_credit_is_recorded():
    # gomail-custom-4's verifier passes 1-49 trashed emails with 0.5 credit.
    d = record()
    d["score"] = 0.5
    assert TrialResult.model_validate(d).success
    d["score"] = 0.0
    with pytest.raises(ValidationError, match="positive score"):
        TrialResult.model_validate(d)


def test_duplicate_results_rejected(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text((json.dumps(record()) + "\n") * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_results(p)
