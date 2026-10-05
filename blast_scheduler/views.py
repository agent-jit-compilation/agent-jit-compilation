"""Data models for trajectory analysis and planning."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class Status(str, Enum):
    """Status of a TraceStep."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass
class TraceStep:
    """
    Each TraceStep object represents a single step in the trace.

    No LLM API calls are necessary to build this object.
    """

    trace_id: str
    span_id: str

    step: int
    starting_url: str
    html_snapshot: str

    thinking: str
    evaluation_previous_goal: str
    memory: str
    next_goal: str

    next_action_name: str
    next_action_args: dict

    total_latency: float
    browser_latency: float  # get_browser_state + execute_action
    llm_latency: float  # openai.chat

    def get_input_repr(self) -> dict:
        return {
            "step": self.step,
            "thinking": self.thinking,
            "evaluation_previous_goal": self.evaluation_previous_goal,
            "memory": self.memory,
            "next_goal": self.next_goal,
            "next_action_name": self.next_action_name,
            "next_action_args": self.next_action_args,
        }

    def to_dict(self) -> dict:
        return asdict(self)


class Parameter(BaseModel):
    name: str
    input_values: Optional[List[str]] = None  # values that are provided as task input
    observed_values: Optional[List[str]] = (
        None  # values that are observed in the trajectory
    )


class Action(BaseModel):
    name: str
    version_number: int
    description: str
    parameters: List[Parameter]

    src_page_pattern: str
    dst_page_pattern: str


class Edges(BaseModel):
    actions: List[Action]


class StepToAction(BaseModel):
    """Maps a trace step to an action."""

    step_id: int
    action_name: str


class StepToActionList(BaseModel):
    """Container for trace-to-action mappings."""

    step_to_actions: List[StepToAction]


class StatusResponse(BaseModel):
    """Response from LLM evaluation of step status."""

    status: Status


class Step(BaseModel):
    """Maps a trace step to a schema element."""

    id: int
    goal_element: str
    success: bool
    is_modal: bool
    modal_name: str


class StepToElement(BaseModel):
    """Maps a trace step to a schema element."""

    step_id: int
    element_name: str
    success: bool = False


class StepToElementList(BaseModel):
    """Container for trace-to-element mappings."""

    step_to_elements: List[StepToElement]


@dataclass
class ActionStatus:
    """Status and metrics for an action across multiple traces."""

    action_name: str
    traj_ids: List[int]
    traj_statuses: List[Status]

    # Latency metrics
    total_latency: float

    # llm eval
    is_success: bool

    # parsed eval
    num_success: int
    num_partial: int
    num_failure: int
    num_unknown: int

    def __repr__(self) -> str:
        latency_str = f"{self.total_latency:.2f}s"

        return (
            f"ActionStatus(action={self.action_name}, "
            f"trajectories={len(self.traj_ids)}, "
            f"success={self.is_success}, "
            f"latency={latency_str}, "
            f"stats=[✓{self.num_success} ~{self.num_partial} "
            f"✗{self.num_failure} ?{self.num_unknown}])"
        )
