"""Versioned trial records. Every attempted trial, including setup failures, is retained."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Timing(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    setup_s: float = Field(ge=0)
    online_s: float = Field(
        ge=0,
        description="Task submission through agent completion; includes online planning/scheduling",
    )
    evaluation_s: float = Field(ge=0)
    total_wall_s: float = Field(ge=0)
    planning_s: float | None = Field(default=None, ge=0)
    execution_s: float | None = Field(default=None, ge=0)


class TrialResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["fresh_run", "mock_test", "historical_import"]
    run_id: str
    task_id: str
    implementation_id: str
    benchmark: str
    benchmark_revision: str
    method: str
    method_revision: str
    artifact_revision: str
    model: str | None
    cross_model_profile: bool = False
    trial: int = Field(ge=0)
    status: Literal[
        "success",
        "failure",
        "timeout",
        "setup_error",
        "agent_error",
        "evaluation_error",
        "budget_exhausted",
    ]
    success: bool
    score: float | None = Field(ge=0, le=1)
    error: str | None = None
    started_at: datetime
    timing: Timing
    configuration: dict[str, Any]
    resources: dict[str, Any]
    reset: dict[str, Any]
    evaluator: dict[str, Any]
    usage: dict[str, Any]
    trace: dict[str, Any]

    @model_validator(mode="after")
    def consistent(self):
        if self.success != (self.status == "success"):
            raise ValueError("Success and status disagree")
        if self.success and self.score != 1:
            raise ValueError("Success requires full credit")
        if (
            self.status in ("timeout", "setup_error", "agent_error", "evaluation_error")
            and self.score != 0
        ):
            raise ValueError("Infrastructure/timeout attempts must retain zero score")
        if (self.score is None) != (self.status == "budget_exhausted"):
            raise ValueError(
                "Only budget-exhausted attempts have an unscored (null) result"
            )
        if self.kind == "mock_test" and self.benchmark != "mock":
            raise ValueError("Mock results cannot claim a live benchmark")
        return self
