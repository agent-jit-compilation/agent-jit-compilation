"""Adapters implement async run(context) -> AgentOutput; see docs/ADDING_AN_AGENT.md."""

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


@dataclass
class AgentOutput:
    """Return an answer and optional resources that outlive agent execution.

    Attributes:
        text: Answer passed to the task evaluator; not a success assertion.
        planning_s: Online planning/scheduling time in seconds, when measured.
        execution_s: Execution subinterval in seconds, when measured.
        metadata: JSON-serializable method diagnostics stored in the trial.
        state_provider: Async callback collecting final benchmark state while
            worker browsers are still open. Called during evaluation.
        cleanup: Async callback releasing adapter-owned resources after
            evaluation, including evaluation failure. The runner owns its own
            browser; adapters remain responsible for extra worker resources.
    """

    text: str
    planning_s: float | None = None
    execution_s: float | None = None
    metadata: dict = field(default_factory=dict)
    state_provider: Callable[[], Awaitable[dict]] | None = None
    cleanup: Callable[[], Awaitable[None]] | None = None


@dataclass
class RunContext:
    """Inputs supplied to an adapter for one isolated benchmark attempt.

    Attributes:
        task: Catalog entry with instruction, IDs, and evaluator metadata.
        page: Live Patchright page in the runner's fresh browser profile.
        cdp_url: Endpoint for attaching another driver to that same browser.
        root: Artifact directory containing benchmarks and cached inputs.
        output_dir: Trial-specific directory for diagnostics and traces.
        configuration: Resolved evaluation configuration, including budgets.
    """

    task: dict
    page: Any
    cdp_url: str
    root: Path
    output_dir: Path
    configuration: dict
