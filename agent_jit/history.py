"""Save local Browser-Use histories for diagnosis and offline profiling."""

import json
from pathlib import Path
from typing import Any, Protocol


class SerializableHistory(Protocol):
    """Serialization boundary that does not import the optional browser package."""

    def model_dump(self) -> dict[str, Any]:
        """Return the native JSON-compatible history representation."""
        ...


def save_history(history: SerializableHistory | None, output: Path, name: str) -> None:
    """Save a Browser-Use history for trace processing and debugging.

    Args:
        history: Browser-Use AgentHistoryList, including partial failed runs.
        output: Trial directory, normally inside the ignored runs directory.
        name: A basename identifying the worker within this trial.

    Raises:
        ValueError: The worker name is not a safe basename.
    """
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("History name must be a basename")
    if history is None:
        return
    target = output / "browser-histories"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{output.name}-{name}.json").write_text(
        json.dumps(history.model_dump(), indent=2, allow_nan=False) + "\n"
    )
