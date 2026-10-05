"""@ronyw: Not currently used."""
from typing import List, Dict, Literal
import openai

from blast_scheduler.views import Action, Status, StatusResponse, TraceStep
from blast_scheduler.prompts import (
    SYSTEM_PROMPT_EVALUATE_STEP,
    USER_PROMPT_EVALUATE_STEP,
)


def extract_status(evaluation_previous_goal: str) -> Status:
    """Extract status from evaluation_previous_goal text."""
    text_lower = evaluation_previous_goal.lower()
    if "partial" in text_lower:
        return Status.PARTIAL
    elif "failure" or "not success" in text_lower:
        return Status.FAILURE
    elif "success" in text_lower:
        return Status.SUCCESS
    else:
        return Status.UNKNOWN


def evaluate_step(
    client: openai.OpenAI,
    trace_steps: List[TraceStep],
    actions: Dict[str, Action],
    step_id: int,
    mode: Literal["extract", "evaluate"] = "extract",
) -> Status:
    """Evaluate the status of a trajectory step using text extraction or LLM evaluation."""
    current_step = trace_steps[step_id]
    if step_id + 1 >= len(trace_steps):
        return Status.UNKNOWN

    next_step = trace_steps[step_id + 1]
    action_name = getattr(current_step, "action_name", None)

    if not action_name or action_name not in actions:
        return Status.UNKNOWN

    if mode == "extract":
        return extract_status(next_step.evaluation_previous_goal)
    elif mode == "evaluate":
        action_description = actions[action_name].description

        resp = client.responses.parse(
            model="gpt-4.1",
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT_EVALUATE_STEP,
                },
                {
                    "role": "user",
                    "content": USER_PROMPT_EVALUATE_STEP.format(
                        logical_step_description=action_description,
                        current_step=f"<thinking>{current_step.thinking}</thinking>\n\n<memory>{current_step.memory}</memory>\n\n<next_goal>{current_step.next_goal}</next_goal>",
                        next_step=next_step.evaluation_previous_goal,
                    ),
                },
            ],
            text_format=StatusResponse,
        )
        result: StatusResponse = resp.output_parsed
        return result.status
    else:
        raise ValueError(f"Invalid mode: {mode}")
