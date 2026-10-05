"""Parse exported traces and label their steps with original scheduler prompts."""

import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

from agent_jit.budget import Ledger
from agent_jit.predict import BudgetedResponses
from blast_scheduler.laminar.trace_parser import TraceParser
from blast_scheduler.offline.site import SiteRep
from blast_scheduler.offline.site_graph_builder import ContextBuilder
from blast_scheduler.offline.step_processor import process_step
from blast_scheduler.views import TraceStep


def _hashes(paths: list[Path]) -> list[dict]:
    return [
        {"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(set(paths))
    ]


def _browser_history_steps(path: Path) -> tuple[list[TraceStep], dict]:
    """Adapt native histories to the original history parser with explicit timing limits."""
    raw = json.loads(path.read_text())
    history = raw.get("history")
    if not isinstance(history, list) or not history:
        raise ValueError(f"No browser history in {path.name}")
    kept, indices, excluded = [], [], []
    for index, item in enumerate(history):
        state = item.get("state") or {}
        if index == 0 and state.get("title") == "Initial Actions":
            # Browser-Use 0.7.10 records these timestamps after navigation, so
            # their difference is not a measurement of the setup action.
            excluded.append(index)
            continue
        metadata = item.get("metadata") or {}
        start, end = metadata.get("step_start_time"), metadata.get("step_end_time")
        if (
            any(
                not isinstance(t, (int, float))
                or isinstance(t, bool)
                or not math.isfinite(t)
                for t in (start, end)
            )
            or end < start
        ):
            raise ValueError(
                f"Missing or invalid measured timing in {path.name}, step {index}"
            )
        kept.append(
            {**item, "state": state, "model_output": item.get("model_output") or {}}
        )
        indices.append(index)
    if not kept:
        raise ValueError(f"No measured agent steps in {path.name}")
    start = min(s["metadata"]["step_start_time"] for s in kept)
    end = max(s["metadata"]["step_end_time"] for s in kept)
    span = {
        "name": "agent.run",
        "spanId": "local-history",
        "parentSpanId": "0",
        "startTime": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "endTime": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "output": {"history": kept},
    }
    # This envelope is only a temporary format bridge for the original parser;
    # it is not exported as a purported Laminar observation.
    with tempfile.TemporaryDirectory(prefix="agent-jit-history-parse-") as tmp:
        bridge = Path(tmp) / path.name
        bridge.write_text(json.dumps({"spans": [span]}, allow_nan=False))
        steps = TraceParser(str(bridge)).trace_steps
    return steps, {
        "source_step_indices": indices,
        "excluded_initial_action_indices": excluded,
    }


def parse_traces(input_dir: Path, output: Path, trace_format: str = "auto") -> dict:
    """Extract step observations from exports or local histories without model calls.

    Args:
        input_dir: Directory containing only `<trace_id>.json` raw exports or histories.
        output: New directory for `steps/` and build provenance.
        trace_format: `auto` preserves the original parser's preference for
            agent-run history; `chat-spans` or `agent-run` explicitly selects a
            historical representation when both occur in the same export.
            `browser-history` reads locally saved Browser-Use history files.

    Returns:
        Report containing input hashes and the number of extracted steps.

    Raises:
        ValueError: If no traces/steps are found or output already exists.
    """
    if trace_format not in ("auto", "chat-spans", "agent-run", "browser-history"):
        raise ValueError("Unsupported trace format")
    files = sorted(input_dir.glob("*.json"))
    if not files or output.exists():
        raise ValueError("Provide raw trace JSON files and a new output directory")
    report = {
        "kind": "offline_trace_parsing",
        "schema_version": "1.0",
        "status": "failed",
        "model_calls": 0,
        "trace_format": trace_format,
        "inputs": _hashes(files),
        "steps": {},
        "history_mapping": {},
    }
    (output / "steps").mkdir(parents=True)
    start = time.perf_counter()
    diagnostics = io.StringIO()
    try:
        for path in files:
            with contextlib.redirect_stdout(diagnostics):
                if trace_format == "browser-history":
                    steps, mapping = _browser_history_steps(path)
                    report["history_mapping"][path.stem] = mapping
                else:
                    parser = TraceParser(str(path))
                    if trace_format == "chat-spans":
                        steps = parser._build_steps_from_openai_chat()
                    elif trace_format == "agent-run":
                        steps = parser._build_steps_from_agent_run()
                    else:
                        steps = parser.trace_steps
            if not steps:
                raise ValueError(f"No supported agent steps in trace {path.name}")
            for index, step in enumerate(steps):
                if step.step != index or step.trace_id != path.stem:
                    raise ValueError(f"Trace identity/index mismatch in {path.name}")
            (output / "steps" / path.name).write_text(
                json.dumps([s.to_dict() for s in steps], indent=2, allow_nan=False)
                + "\n"
            )
            report["steps"][path.stem] = len(steps)
        report["status"] = "success"
        return report
    finally:
        report["offline_s"] = time.perf_counter() - start
        (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "build.log").write_text(diagnostics.getvalue())


def label_traces(
    trace_steps: Path,
    schema_root: Path,
    application: str,
    output: Path,
    model: str,
    ledger_path: Path,
    budget_usd: float,
    max_retries: int = 10,
    max_requests: int = 1024,
) -> dict:
    """Map parsed steps to schema elements using the original labeling prompt.

    Runs sequentially to keep budget failures explicit. Unlike the original
    notebook's batch helper, a failed label stops the build rather than silently
    excluding a step. All completed labels remain in the failed build report.
    A consumable site representation is written only after every step is labeled.

    Args:
        trace_steps: Directory of parsed trace JSON lists from `parse-traces`.
        schema_root: Parent of the application's page-schema directory.
        application: Schema subdirectory and resulting site name.
        output: New directory for mappings and provenance.
        model: Model used to label traces, not necessarily their execution model.
        ledger_path: Shared ledger for all model calls in the campaign.
        budget_usd: Maximum cumulative accounted charge for that ledger.
        max_retries: Attempts per label with the original correction prompt.

    Returns:
        Build report containing every label and exact input hashes.

    Raises:
        ValueError: If inputs are incomplete or a valid label cannot be obtained.
    """
    if Path(application).name != application or application in (".", ".."):
        raise ValueError("Application must be a plain schema directory name")
    if not 1 <= max_retries <= 10:
        raise ValueError("Label attempts must be in 1..10")
    files = sorted(trace_steps.glob("*.json"))
    if not files or output.exists():
        raise ValueError("Provide parsed traces and a new output directory")
    parsed = {}
    for path in files:
        steps = [TraceStep(**item) for item in json.loads(path.read_text())]
        if not steps or any(
            s.trace_id != path.stem or s.step != i for i, s in enumerate(steps)
        ):
            raise ValueError(
                f"Trace identity/index mismatch or empty trace: {path.name}"
            )
        parsed[path.stem] = steps
    schema_files = sorted((schema_root / application).glob("*.json"))
    if not schema_files:
        raise ValueError("No page schemas found for the application")
    # Loading saved schemas requires no provider client or network access.
    context = ContextBuilder(
        application, openai_client=object(), schema_root=str(schema_root)
    )
    diagnostics = io.StringIO()
    urls = sorted(
        {s.starting_url for steps in parsed.values() for s in steps if s.starting_url}
    )
    with contextlib.redirect_stdout(diagnostics):
        schemas = context.load_page_schemas(urls)
    missing_urls = [url for url in urls if url not in schemas]
    if not schemas or missing_urls:
        raise ValueError(
            "URL mappings do not cover all observed trace pages: "
            + ", ".join(missing_urls[:3])
            + f". Review {schema_root / application / 'url_mappings.json'}"
        )
    report = {
        "kind": "offline_trace_labeling",
        "schema_version": "1.0",
        "status": "failed",
        "labeling_model": model,
        "inputs": _hashes(files + schema_files),
        "labels": {},
        "success_filter": "None: original site builder retains labels regardless of step success.",
    }
    client = BudgetedResponses(model, Ledger(ledger_path, budget_usd, max_requests))
    output.mkdir(parents=True)
    start = time.perf_counter()
    site = SiteRep(application)
    try:
        for trace_id, steps in parsed.items():
            report["labels"][trace_id] = []
            for step in steps:
                with contextlib.redirect_stdout(diagnostics):
                    label = process_step(
                        client,
                        step,
                        schemas,
                        max_depth=None,
                        max_retries=max_retries,
                        model=model,
                    )
                if label is None or label.id != step.step:
                    raise ValueError(
                        f"Missing or mismatched label for {trace_id}, step {step.step}"
                    )
                report["labels"][trace_id].append(label.model_dump())
                site.add_mapping(label.goal_element, trace_id, [label.id])
        (output / "site_rep.json").write_text(
            json.dumps(site.to_dict(), indent=2) + "\n"
        )
        report["status"] = "success"
        return report
    finally:
        report["offline_s"] = time.perf_counter() - start
        (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "build.log").write_text(diagnostics.getvalue())
        client.close()
