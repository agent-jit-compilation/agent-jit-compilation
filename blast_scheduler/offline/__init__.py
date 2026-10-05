"""Original package exports, loaded lazily to keep offline commands lightweight."""

from importlib import import_module

_EXPORTS = {
    "format_page_information": (
        "blast_scheduler.offline.schema",
        "format_page_information",
    ),
    "list_defs": ("blast_scheduler.offline.schema", "list_defs"),
    "produce_json_schema": ("blast_scheduler.offline.schema", "produce_json_schema"),
    "SiteRep": ("blast_scheduler.offline.site", "SiteRep"),
    "ContextBuilder": ("blast_scheduler.offline.site_graph_builder", "ContextBuilder"),
    "MultiTraceProcessor": (
        "blast_scheduler.offline.site_graph_builder",
        "MultiTraceProcessor",
    ),
    "TraceProcessor": ("blast_scheduler.offline.site_graph_builder", "TraceProcessor"),
    "process_step": ("blast_scheduler.offline.step_processor", "process_step"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
