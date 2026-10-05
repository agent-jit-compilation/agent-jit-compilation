"""Original package exports, loaded lazily to keep offline commands lightweight."""

from importlib import import_module

_EXPORTS = {
    "TraceParser": (".trace_parser", "TraceParser"),
    "TraceScraper": (".trace_scraper", "TraceScraper"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
