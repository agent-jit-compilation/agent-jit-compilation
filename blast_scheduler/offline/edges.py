"""@deprecated: This module is no longer used in the new schema-based approach.

The offline module now maps trace steps directly to schema elements instead of actions.
See step_processor.py and site_graph_builder.py for the new implementation.
"""

from typing import Dict, List

import openai

from blast_scheduler.prompts import (
    SYSTEM_PROMPT_ASSIGN_STEPS,
    SYSTEM_PROMPT_PRODUCE_EDGES,
    USER_PROMPT_ASSIGN_STEPS,
    USER_PROMPT_PRODUCE_EDGES,
)
from blast_scheduler.views import Action, Edges, StepToActionList, TraceStep


def format_trace_steps(trace_steps: List[TraceStep]) -> str:
    """Format trace steps for LLM prompts."""
    trace_steps_text = ""
    for step in trace_steps:
        trace_steps_text += str(step.step) + " "
        trace_steps_text += str(step.starting_url) + " "
        trace_steps_text += str(step.evaluation_previous_goal) + " "
        trace_steps_text += str(step.next_action_name) + " "
        trace_steps_text += ";\n"

    return trace_steps_text


def format_existing_edges(edges: Dict[str, Action]) -> str:
    """Format existing edges for LLM prompts."""
    if not edges:
        return ""

    existing_edges_str = "\n\nEXISTING EDGES:\n"
    for edge_name, edge in edges.items():
        params_str = ", ".join([f"{p.name}" for p in edge.parameters])
        existing_edges_str += f"- {edge.name}({params_str}): {edge.description}\n"
        existing_edges_str += f"  {edge.src_page_pattern} -> {edge.dst_page_pattern}\n"

    existing_edges_str += "\n\nIMPORTANT: Only reuse these edges they are exactly the same as the ones used in the trace steps for the current task. DO not include in the output if they are not used in the trace steps for the current task.\n"

    print(existing_edges_str)
    return existing_edges_str


def produce_edges(
    client: openai.OpenAI, task: str, page_information: str, trajectories: str
) -> Edges:
    resp = client.responses.parse(
        model="gpt-4.1",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT_PRODUCE_EDGES},
            {
                "role": "user",
                "content": USER_PROMPT_PRODUCE_EDGES.format(
                    task=task,
                    page_information=page_information,
                    trajectories=trajectories,
                ),
            },
        ],
        text_format=Edges,
    )
    return resp.output_parsed


def assign_step_to_actions(
    client: openai.OpenAI,
    task: str,
    actions: List[Action],
    trace_step_str: str,
    step_count: int,
    retry_hint: str = "",
):
    actions_str = ""
    for action in actions:
        params_str = ", ".join([f"{p.name}" for p in action.parameters])
        actions_str += f"- {action.name}({params_str}): {action.description}\n"
        actions_str += f"  {action.src_page_pattern} -> {action.dst_page_pattern}\n"

    user_content = USER_PROMPT_ASSIGN_STEPS.format(
        task=task, actions=actions_str, trace_steps=trace_step_str
    )

    # Add explicit count to the prompt
    user_content += f"\n\nTOTAL TRACE STEPS TO MAP: {step_count}"
    if retry_hint:
        user_content += f"\n\nRETRY HINT: {retry_hint}"

    resp = client.responses.parse(
        model="gpt-4.1",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT_ASSIGN_STEPS},
            {"role": "user", "content": user_content},
        ],
        text_format=StepToActionList,
    )
    return resp.output_parsed
