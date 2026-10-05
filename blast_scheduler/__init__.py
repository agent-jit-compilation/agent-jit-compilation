"""Original package exports, loaded lazily to keep offline commands lightweight."""

from importlib import import_module

_EXPORTS = {
    "TraceProcessor": (".offline.site_graph_builder", "TraceProcessor"),
    "ContextBuilder": (".offline.site_graph_builder", "ContextBuilder"),
    "MultiTraceProcessor": (".offline.site_graph_builder", "MultiTraceProcessor"),
    "TraceStep": (".views", "TraceStep"),
    "Action": (".views", "Action"),
    "ActionStatus": (".views", "ActionStatus"),
    "Status": (".views", "Status"),
    "Parameter": (".views", "Parameter"),
    "SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS": (
        ".prompts",
        "SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS",
    ),
    "USER_PROMPT_IDENTIFY_LOGICAL_STEPS": (
        ".prompts",
        "USER_PROMPT_IDENTIFY_LOGICAL_STEPS",
    ),
    "SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES": (
        ".prompts",
        "SYSTEM_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES",
    ),
    "USER_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES": (
        ".prompts",
        "USER_PROMPT_IDENTIFY_LOGICAL_STEPS_WITH_TRAJECTORIES",
    ),
    "SYSTEM_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS": (
        ".prompts",
        "SYSTEM_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS",
    ),
    "USER_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS": (
        ".prompts",
        "USER_PROMPT_CATEGORIZE_TRAJECTORY_TO_LOGICAL_STEPS",
    ),
    "SYSTEM_PROMPT_EVALUATE_STEP": (".prompts", "SYSTEM_PROMPT_EVALUATE_STEP"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
