"""Original package exports, loaded lazily to keep offline commands lightweight."""

from importlib import import_module

_EXPORTS = {
    "Agent": (".models", "Agent"),
    "Tool": (".models", "Tool"),
    "SMCPTool": (".models", "SMCPTool"),
    "CoreTool": (".models", "CoreTool"),
    "AbstractState": (".models", "AbstractState"),
    "AgentExecutor": (".executor", "AgentExecutor"),
    "create_python_executor": (".coderun", "create_python_executor"),
    "verify_code": (".codecheck", "verify_code"),
    "check_code_candidate": (".codecheck", "check_code_candidate"),
    "compute_code_cost": (".codecost", "compute_code_cost"),
    "LLMFactory": (".llm_factory", "LLMFactory"),
    "create_ask_human_tool": (".tools_hitl", "create_ask_human_tool"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
