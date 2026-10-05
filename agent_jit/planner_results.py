"""Reproduce Table 1 using saved group means and best-cost latency spreads."""

import csv
import json

import numpy as np

from .analysis import fingerprint


def classify(value, bins):
    matches = [
        name
        for name, (low, high) in bins.items()
        if value >= low and (high is None or value <= high)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Task attribute does not resolve to one paper stratum: {value}"
        )
    return matches[0]


def planner_results(root):
    path = root / "data/recorded/planner/table1-snapshot.json"
    reference_path = root / "data/recorded/planner/table1-reference.json"
    catalog_path = root / "benchmarks/tasks.json"
    snapshot = json.loads(path.read_text())
    reference = json.loads(reference_path.read_text())
    files = [path, reference_path, catalog_path]
    catalog = {
        t["implementation_id"]: t for t in json.loads(catalog_path.read_text())["tasks"]
    }
    groups = []
    for group in snapshot["groups"]:
        task = catalog[group["task"]]
        method = next(
            m
            for m in ("loop-tools-baseline", "loop-baseline", "best", "worst")
            if group["configuration"].endswith("-" + m)
        )
        groups.append(
            dict(
                **group,
                method=method,
                model=group["configuration"].removesuffix("-" + method),
                cardinality=classify(
                    task["cardinality"], reference["thresholds"]["cardinality"]
                ),
                length=classify(
                    task["baseline_steps"], reference["thresholds"]["length"]
                ),
            )
        )
    rows = []
    methods = ("loop-baseline", "loop-tools-baseline", "best")
    for published in reference["rows"]:
        dim, label = published["dimension"], published["label"]
        row = dict(dimension=dim, label=label)
        for method in methods:
            selected = [g for g in groups if g["method"] == method and g[dim] == label]
            row[method] = dict(
                latency_s=float(np.mean([g["latency_s"] for g in selected])),
                correctness=float(np.mean([g["correctness"] for g in selected])),
                input_groups=len(selected),
                unique_task_model_pairs=len(
                    {(g["task"], g["model"]) for g in selected}
                ),
                snapshot_group_indices=[
                    i for i, g in enumerate(groups) if g in selected
                ],
            )
            if method == "best":
                row["best_cost_spread_s"] = max(g["latency_s"] for g in selected) - min(
                    g["latency_s"] for g in selected
                )
        row["speedup"] = row["loop-baseline"]["latency_s"] / row["best"]["latency_s"]
        row["accuracy_gain_pp"] = 100 * (
            row["best"]["correctness"] - row["loop-baseline"]["correctness"]
        )
        rows.append(row)
    # The headline baseline gives each model equal weight despite unequal coverage.
    model_rows = [r for r in rows if r["dimension"] == "model"]
    macro = {
        method: {
            field: float(np.mean([r[method][field] for r in model_rows]))
            for field in ("latency_s", "correctness")
        }
        for method in methods
    }
    data = dict(
        kind="historical_table_reconstruction",
        aggregation=snapshot["aggregation"],
        historical_generator=snapshot["historical_generator"],
        range_definition="Maximum minus minimum best-cost task/model group latency within each row.",
        rows=rows,
        model_macro_means=macro,
        headline_speedup=macro["loop-baseline"]["latency_s"]
        / macro["best"]["latency_s"],
        headline_accuracy_gain_pp=100
        * (macro["best"]["correctness"] - macro["loop-baseline"]["correctness"]),
        inputs=fingerprint(files, root),
        groups=groups,
        published_rows=reference["rows"],
    )
    return data


def analyze_planner(root, output):
    data = planner_results(root)
    output.mkdir(parents=True, exist_ok=True)
    (output / "planner-table1.json").write_text(
        json.dumps(data, indent=2, allow_nan=False) + "\n"
    )
    methods = ("loop-baseline", "loop-tools-baseline", "best")
    flat = []
    for row in data["rows"]:
        item = {k: v for k, v in row.items() if k not in methods}
        for method in methods:
            item.update(
                {
                    f"{method}_{k}": v
                    for k, v in row[method].items()
                    if k != "snapshot_group_indices"
                }
            )
        flat.append(item)
    with (output / "planner-table1.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)

    lines = [
        "# Table 1: planner end-to-end results",
        "",
        "Latency and accuracy are means of saved task/model group values. Range is the maximum minus minimum best-cost group latency in each row.",
        "",
        "| Group | Stratum | Browser-Use (s / %) | +cache (s / %) | JIT-Planner (s / %) | Range (s) | Speedup | Accuracy gain (pp) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in data["rows"]:
        cells = [
            f"{row[m]['latency_s']:.1f} / {100 * row[m]['correctness']:.0f}"
            for m in methods
        ]
        lines.append(
            f"| {row['dimension']} | {row['label']} | "
            + " | ".join(cells)
            + f" | {row['best_cost_spread_s']:.1f} | {row['speedup']:.1f}× | {row['accuracy_gain_pp']:+.0f} |"
        )
    (output / "planner-table1.md").write_text("\n".join(lines) + "\n")
    return dict(
        output=str(output.resolve()),
        table_rows=len(data["rows"]),
        input_groups=len(data["groups"]),
        model_calls=0,
    )
