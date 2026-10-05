"""Rebuild original scheduler distributions from labeled trace observations."""

import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import time

from blast_scheduler.offline.element_latencies import LatencyDistributionBuilder
from blast_scheduler.offline.site import SiteRep


def build_profile(site_file: Path, trace_steps: Path, output: Path, model: str) -> dict:
    """Fit model-specific latency distributions without making model calls.

    Args:
        site_file: Original SiteRep JSON mapping elements to trace step indices.
        trace_steps: Directory of parsed `<trace_id>.json` observation lists.
        output: New directory for `element_latencies.json` and build provenance.
        model: Execution model that produced the traces, not the labeling model.

    Returns:
        Build report with input hashes, observation counts, and offline timing.

    Raises:
        ValueError: If mappings or observed latencies are invalid, or output exists.
        FileNotFoundError: If a referenced trace is missing.
    """
    if output.exists():
        raise ValueError(
            "Output directory already exists; choose a new profile directory"
        )
    site = SiteRep.from_file(str(site_file))
    if not model.strip() or not site.element_mappings:
        raise ValueError(
            "An execution model and nonempty element mappings are required"
        )
    traces = {}
    for mappings in site.element_mappings.values():
        if not mappings:
            raise ValueError("Each element must reference at least one trace")
        for trace_id, indices in mappings.items():
            if Path(trace_id).name != trace_id or trace_id in (".", ".."):
                raise ValueError("Trace IDs must be plain filenames")
            if trace_id not in traces:
                traces[trace_id] = json.loads(
                    (trace_steps / f"{trace_id}.json").read_text()
                )
            steps = traces[trace_id]
            if (
                not isinstance(steps, list)
                or not indices
                or len(set(indices)) != len(indices)
            ):
                raise ValueError(
                    "Each mapping requires unique indices into a trace list"
                )
            for index in indices:
                if type(index) is not int or not 0 <= index < len(steps):
                    raise ValueError(f"Invalid step index in trace {trace_id}")
                latency = steps[index]["total_latency"]
                if (
                    isinstance(latency, bool)
                    or not isinstance(latency, (int, float))
                    or not math.isfinite(latency)
                    or latency < 0
                ):
                    raise ValueError(
                        f"Expected a finite nonnegative latency in trace {trace_id}"
                    )

    start = time.perf_counter()
    output.mkdir(parents=True)
    diagnostics = io.StringIO()
    with contextlib.redirect_stdout(diagnostics):
        # The original constructor only warns about its default trace directory.
        # Set the explicit directory before computation; never change global cwd.
        builder = LatencyDistributionBuilder(str(site_file))
        builder.trace_steps_dir = trace_steps
        builder.compute_all_element_latencies()
        builder.fit_distributions()
        builder.save_element_latencies(str(output / "element_latencies.json"))
    inputs = [site_file, *(trace_steps / f"{key}.json" for key in sorted(traces))]
    report = {
        "schema_version": "1.0",
        "kind": "offline_latency_profile_build",
        "execution_model": model,
        "site_name": site.site_name,
        "model_calls": 0,
        "offline_s": time.perf_counter() - start,
        "aggregation": "Sum mapped step total_latency within each trace for each element; one observation per element/trace, then original distribution fitting.",
        "inputs": [
            {
                "path": str(p.resolve()),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in inputs
        ],
        "observations": {
            name: len(latency.values)
            for name, latency in builder.element_latencies.items()
        },
        "sparse_data_rule": "Original fitter uses the first observation as a constant when there are at most three observations.",
    }
    (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "build.log").write_text(diagnostics.getvalue())
    return report
