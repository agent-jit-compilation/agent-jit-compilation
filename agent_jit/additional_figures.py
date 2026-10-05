"""Latency components, protocol failures, and matched parallel/retry analysis."""

import json
from collections import Counter
from statistics import mean

import numpy as np

from .analysis import APPS, MODELS, fingerprint


def figure6(root):
    path = root / "data/recorded/planner/latency-components.json"
    groups = json.loads(path.read_text())["groups"]
    bars = []
    for method in ("loop-baseline", "loop-tools-baseline", "best"):
        for model in ("gpt-4.1", "gemini-2.5-flash", "gemini-2.5-pro"):
            rows = [g for g in groups if g["configuration"] == f"{model}-{method}"]
            timings = [g["avg_timing"] for g in rows]
            bars.append(
                dict(
                    model=model,
                    method=method,
                    groups=len(rows),
                    planning_s=mean(t["planning_seconds"] for t in timings),
                    inference_s=mean(t["llm_total_seconds"] for t in timings),
                    tool_execution_s=mean(
                        max(0, t["execution_seconds"] - t["llm_total_seconds"])
                        for t in timings
                    ),
                )
            )
    return dict(
        figure=6,
        scope="Planner and Browser-Use bars across five applications. CUA component timings are not present in this snapshot.",
        aggregation="Original group averaging: planning, LLM time, and max(0, execution minus LLM time).",
        bars=bars,
        inputs=fingerprint([path], root),
    )


def figure7(root):
    files = [
        p
        for app in APPS
        for p in sorted(
            (root / f"data/recorded/planner/results_{app}").glob("*/*.json")
        )
    ]
    rows = [r for p in files for r in json.loads(p.read_text())]
    mapping = {
        "syntax": "Syntax Error",
        "types": "Type Error",
        "ordering": "Tool Ordering",
        "pre-tools": "Tool Ordering",
        "state-access": "State Mutation",
        "generation": "Timeout",
    }
    conditions = []
    for protocol in (True, False):
        selected = [r for r in rows if r["config"]["with_protocol"] == protocol]
        failures = [r for r in selected if not r["codecheck"]["overall_pass"]]
        counts = Counter(
            mapping.get(t, "Timeout")
            for r in failures
            for t in (r["codecheck"].get("failure_types") or ["generation"])
        )
        conditions.append(
            dict(
                protocol=protocol,
                candidates=len(selected),
                failed_candidates=len(failures),
                failure_rate=len(failures) / len(selected),
                occurrences=dict(counts),
                proportions={k: v / sum(counts.values()) for k, v in counts.items()},
            )
        )
    return dict(
        figure=7,
        scope="Same available REAL samples as Figure 5, all seven models.",
        aggregation="Distribution of failure-type occurrences; one invalid candidate can have several types. Overall candidate failure rate is reported separately.",
        conditions=conditions,
        inputs=fingerprint(files, root),
    )


def figure9(root):
    path = root / "data/recorded/planner/retry-trials.json"
    records = json.loads(path.read_text())["trials"]
    series = []
    for model in ("gpt-5", "gemini-2.5-flash"):
        single = [
            r
            for r in records
            if r["model"] == model
            and r["protocol"]
            and r["condition"] == "retry_single"
        ]
        retry = [
            r
            for r in records
            if r["model"] == model and r["protocol"] and r["condition"] == "retry"
        ]
        if len(single) != 32 or len(retry) != 32:
            raise ValueError(
                "Expected 32 matched-condition samples in each Figure 9 dataset"
            )
        positive = [r["latency_s"] for r in single if r["latency_s"] > 0]
        retry = [r for r in retry if r["latency_s"] > 0]
        times = np.linspace(
            0, max(positive + [r["latency_s"] for r in retry]) * 1.05, 400
        )
        p = mean(r["valid"] for r in single)
        series.append(
            dict(
                model=model,
                single_samples=len(single),
                retry_samples=len(retry),
                parallel=[
                    [
                        float(t),
                        1
                        - (1 - sum(x <= t for x in positive) / len(positive) * p) ** 8,
                    ]
                    for t in times
                ],
                serial=[
                    [
                        float(t),
                        sum(r["valid"] and r["latency_s"] <= t for r in retry)
                        / len(retry),
                    ]
                    for t in times
                ],
            )
        )
    return dict(
        figure=9,
        scope="webarena-747; protocol enabled; matched single-attempt and retry experiment snapshots.",
        aggregation="Parallel: 1-(1-F(t)*p)^8 from single-attempt samples. Serial: empirical success-by-time CDF for up to three attempts with feedback.",
        series=series,
        inputs=fingerprint([path], root),
    )


def plot_additional(data, output):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    number = data["figure"]
    if number == 9:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), squeeze=False)
        for ax, series in zip(axes[0], data["series"], strict=True):
            for key, label, color in [
                ("parallel", "Parallel hedging (k=8)", "#27ae60"),
                ("serial", "Serial retry (≤3)", "#e74c3c"),
            ]:
                ax.plot(*zip(*series[key]), label=label, color=color)
            ax.set(
                title=MODELS[series["model"]][0],
                xlabel="Latency budget (s)",
                ylabel="Valid-plan rate",
                ylim=(0, 1.03),
            )
            ax.grid(alpha=0.2)
        axes[0, 1].legend(fontsize=8)
    elif number == 7:
        fig, ax = plt.subplots(figsize=(8, 3.5))
        names = [
            "Tool Ordering",
            "Timeout",
            "Syntax Error",
            "Type Error",
            "State Mutation",
        ]
        colors = ["#9b59b6", "#95a5a6", "#e74c3c", "#f39c12", "#3498db"]
        for i, row in enumerate(data["conditions"]):
            left = 0
            for name, color in zip(names, colors, strict=True):
                width = row["proportions"].get(name, 0)
                ax.barh(
                    i, width, left=left, color=color, label=name if i == 0 else None
                )
                left += width
        ax.set(
            yticks=[0, 1],
            yticklabels=["With protocol", "Without protocol"],
            xlabel="Fraction of failure-type occurrences",
            xlim=(0, 1),
        )
        ax.invert_yaxis()
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.23), ncol=3, fontsize=8)
    else:
        fig, ax = plt.subplots(figsize=(9, 5))
        names = {
            "loop-baseline": "Browser-Use",
            "loop-tools-baseline": "Browser-Use +cache",
            "best": "JIT-Planner",
        }
        models = ["gpt-4.1", "gemini-2.5-flash", "gemini-2.5-pro"]
        hatches = ["", "///", "\\\\"]
        colors = ["#ff6b6b", "#4ecdc4", "#ffa726"]
        for i, row in enumerate(data["bars"]):
            bottom = 0
            for field, color in zip(
                ["planning_s", "inference_s", "tool_execution_s"], colors, strict=True
            ):
                ax.bar(
                    i,
                    row[field],
                    bottom=bottom,
                    color=color,
                    hatch=hatches[models.index(row["model"])],
                )
                bottom += row[field]
        ax.set(xticks=[1, 4, 7], xticklabels=list(names.values()), ylabel="Latency (s)")
        handles = [
            Patch(facecolor=c, label=label)
            for c, label in zip(
                colors, ["Planning", "Inference", "Tool execution"], strict=True
            )
        ]
        handles += [
            Patch(facecolor="white", edgecolor="gray", hatch=h, label=MODELS[m][0])
            for m, h in zip(models, hatches, strict=True)
        ]
        ax.legend(handles=handles, ncol=3, fontsize=8)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(output / f"figure{number}.{suffix}", dpi=180, bbox_inches="tight")
    plt.close(fig)
