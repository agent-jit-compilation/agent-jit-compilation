"""Recorded-result analysis. No model calls; all output inputs are fingerprinted."""

import hashlib
import json
from collections import defaultdict
from math import comb
from statistics import mean

MODELS = {
    "openai/gpt-oss-120b": ("GPT-OSS-120B", "#AED6F1"),
    "gpt-4.1": ("GPT-4.1", "#5499C7"),
    "gpt-5-mini": ("GPT-5-mini", "#2874A6"),
    "gpt-5": ("GPT-5", "#154360"),
    "gemini-2.0-flash-lite": ("Gemini-2.0-Flash-Lite", "#ABEBC6"),
    "gemini-2.5-flash": ("Gemini-2.5-Flash", "#28B463"),
    "gemini-2.5-pro": ("Gemini-2.5-Pro", "#196F3D"),
}
SCHEDULER_COLORS = {
    "GPT-4.1": "#3489db",
    "Gemini-2.5-Pro": "#16a085",
    "Gemini-2.5-Flash": "#f39c12",
    "Anthropic CUA": "#9b59b6",
    "OpenAI CUA": "#e67e22",
}
APPS = ("dashdish", "gomail", "omnizon")


def fingerprint(paths, root):
    return [
        {
            "path": str(p.relative_to(root)),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(set(paths))
    ]


def pass_at_k(n, c, k):
    if not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError("Require 0 <= c <= n and 1 <= k <= n")
    return 0.0 if c == 0 else 1.0 if n - c < k else 1 - comb(n - c, k) / comb(n, k)


def figure5(root):
    groups = defaultdict(list)
    files = [
        p
        for app in APPS
        for p in sorted(
            (root / f"data/recorded/planner/results_{app}").glob("*/*.json")
        )
    ]
    for p in files:
        for row in json.loads(p.read_text()):
            key = (row["config"]["model"], row["config"]["with_protocol"])
            groups[key].append(row)
    points = []
    for model in MODELS:
        for protocol in (True, False):
            rows = groups[model, protocol]
            if not rows:
                raise ValueError(f"Missing samples for {model}, protocol={protocol}")
            valid = sum(bool(r["codecheck"]["overall_pass"]) for r in rows)
            points.append(
                dict(
                    model=model,
                    protocol=protocol,
                    n=len(rows),
                    valid=valid,
                    accuracy=valid / len(rows),
                    latency_s=mean(r["generation_latency"] for r in rows),
                )
            )
    return dict(
        figure=5,
        scope="Available recorded REAL plan-generation samples; coverage is unequal across models.",
        aggregation="Sample-weighted valid-plan fraction; arithmetic mean generation latency, including failed candidates.",
        points=points,
        inputs=fingerprint(files, root),
    )


def figure8(root):
    series = []
    files = []
    for model in ("gemini-2.5-pro", "gpt-4.1", "gpt-5"):
        path = (
            root
            / f"data/recorded/planner/results_gitlab/{model}/webarena-747_{model}.json"
        )
        files.append(path)
        rows = json.loads(path.read_text())
        for protocol in (True, False):
            samples = [r for r in rows if r["config"]["with_protocol"] == protocol]
            n = len(samples)
            c = sum(bool(r["codecheck"]["overall_pass"]) for r in samples)
            if n != 32:
                raise ValueError(f"Expected 32 candidates: {path}, {protocol}, got {n}")
            lats = sorted(
                r["generation_latency"]
                for r in samples
                if r.get("generation_latency", 0) > 0
            )
            ts = [i * 48 / 399 for i in range(400)]
            series.append(
                dict(
                    model=model,
                    protocol=protocol,
                    n=n,
                    valid=c,
                    k=[[k, pass_at_k(n, c, k)] for k in range(1, n + 1)],
                    t=[
                        [
                            t,
                            1
                            - (1 - sum(x <= t for x in lats) / len(lats) * c / n) ** 8,
                        ]
                        for t in ts
                    ],
                )
            )
    return dict(
        figure=8,
        task="webarena-747",
        workers=8,
        aggregation="Pass@k = 1-C(n-c,k)/C(n,k). Pass@t = 1-(1-F(t)*c/n)^8, following the published plotting script; an independence-based estimate, not an observed eight-worker run.",
        series=series,
        inputs=fingerprint(files, root),
    )


def figure10(root, normalize_non_parallel=False):
    base = root / "data/recorded/scheduler"
    path = base / "trials.json"
    trials = json.loads(path.read_text())["trials"]
    files = [path]
    groups = defaultdict(list)
    for r in trials:
        groups[r["application"], r["model"], r["task"], r["stage_name"]].append(r)
    points = []
    replacements = []
    task_means = []
    model_names = {
        "gpt-4.1": "GPT-4.1",
        "gemini-2.5-pro": "Gemini-2.5-Pro",
        "gemini-2.5-flash": "Gemini-2.5-Flash",
    }
    strategy_names = {
        "baseline": "Serial",
        "first_of_n_only": "Hedge",
        "task_parallelism_only": "Parallel",
    }
    for model, label in model_names.items():
        stats = {}
        for (app, m, task, strategy), raw in groups.items():
            if m != model:
                continue
            rows = raw
            # Optional historical sensitivity analysis; the original CLI defaults to False.
            if (
                normalize_non_parallel
                and strategy == "task_parallelism_only"
                and any(
                    r["metrics"].get("tasks", {}).get("completed") == 1 for r in rows
                )
            ):
                rows = groups[app, m, task, "baseline"]
                replacements.append(
                    {
                        "application": app,
                        "model": m,
                        "task": task,
                        "reason": "At least one parallel trial completed one subtask; original analysis substitutes serial trials.",
                    }
                )
            stats[app, task, strategy] = (
                mean(r["total_time"] for r in rows),
                mean(r["evaluated_success"] is True for r in rows),
                len(rows),
            )
            task_means.append(
                dict(
                    application=app,
                    model=m,
                    task=task,
                    strategy=strategy,
                    latency_s=stats[app, task, strategy][0],
                    accuracy=stats[app, task, strategy][1],
                    raw_trials=len(raw),
                    effective_trials=len(rows),
                )
            )
        for strategy, name in strategy_names.items():
            vals = [v for k, v in stats.items() if k[2] == strategy]
            if len(vals) != 27:
                raise ValueError(f"Incomplete {model}/{strategy} task coverage")
            points.append(
                dict(
                    model=label,
                    strategy=name,
                    tasks=27,
                    latency_s=mean(x[0] for x in vals),
                    accuracy=mean(x[1] for x in vals),
                    effective_trials=sum(x[2] for x in vals),
                    source_kind="recomputed_trials",
                )
            )
        selected = []
        overhead = []
        for app in APPS:
            p = base / f"scheduler-{app}_{model}.json"
            files.append(p)
            entries = json.loads(p.read_text())
            if len(entries) != 9 or len({e["task"] for e in entries}) != 9:
                raise ValueError("Incomplete scheduler choices")
            for e in entries:
                latency, accuracy, n = stats[app, e["task"], e["selected_strategy"]]
                selected.append((latency + e["overhead"], accuracy))
                overhead.append(e["overhead"])
        points.append(
            dict(
                model=label,
                strategy="JIT-Scheduler",
                tasks=27,
                latency_s=mean(x[0] for x in selected),
                accuracy=mean(x[1] for x in selected),
                overhead_s=mean(overhead),
                source_kind="recomputed_trials_with_recorded_choices",
            )
        )
        keys = sorted({(k[0], k[1]) for k in stats})
        oracle = [
            min(
                [stats[app, task, s] for s in strategy_names],
                key=lambda v: (v[0], -v[1]),
            )
            for app, task in keys
        ]
        points.append(
            dict(
                model=label,
                strategy="Oracle-Scheduler",
                tasks=27,
                latency_s=mean(x[0] for x in oracle) + mean(overhead),
                accuracy=mean(x[1] for x in oracle),
                overhead_s=mean(overhead),
                source_kind="recomputed_task_mean_oracle",
            )
        )
    cua_path = base / "cua-trials.json"
    files.append(cua_path)
    cua_groups = defaultdict(list)
    for row in json.loads(cua_path.read_text())["trials"]:
        cua_groups[row["model"], row["application"], row["task"]].append(row)
    for model in ("OpenAI CUA", "Anthropic CUA"):
        per_task = [rows for (m, _, _), rows in cua_groups.items() if m == model]
        if len(per_task) != 27 or any(len(rows) != 3 for rows in per_task):
            raise ValueError(f"Incomplete {model} task coverage")
        points.append(
            dict(
                model=model,
                strategy="Serial",
                tasks=27,
                effective_trials=81,
                latency_s=mean(
                    mean(r["total_time"] for r in rows) for rows in per_task
                ),
                accuracy=mean(
                    mean(r["evaluated_success"] is True for r in rows)
                    for rows in per_task
                ),
                source_kind="recomputed_trials",
            )
        )
    reference = base / "camera-ready-coordinates.json"
    files.append(reference)
    ref = json.loads(reference.read_text())
    published = []
    differences = []
    for model, strategies in ref["data"].items():
        for strategy, (lat, acc) in strategies.items():
            latency = lat + (
                ref["overhead"].get(model, 0)
                if strategy in ("JIT-Scheduler", "Oracle-Scheduler")
                else 0
            )
            published.append(
                dict(model=model, strategy=strategy, latency_s=latency, accuracy=acc)
            )
            p = next(
                (p for p in points if (p["model"], p["strategy"]) == (model, strategy)),
                None,
            )
            if p:
                differences.append(
                    dict(
                        model=model,
                        strategy=strategy,
                        latency_delta_s=p["latency_s"] - latency,
                        accuracy_delta=p["accuracy"] - acc,
                    )
                )
            else:
                raise ValueError(f"Missing trial-derived point: {model}/{strategy}")
    return dict(
        figure=10,
        scope="Reanalysis of 728 scheduler trials and 162 CUA baseline trials over 27 REAL tasks. One Gemini Pro parallel trial is absent.",
        reproduction_status=(
            "Optional normalized sensitivity analysis; differs from the camera-ready configuration."
            if normalize_non_parallel
            else "All 17 points recomputed from trials; small residual differences from the camera-ready coordinates are retained, not replaced with constants."
        ),
        aggregation="Binary evaluated_success, equal-weight task means including failures. Recorded overhead added to JIT and oracle. Parallel-to-serial substitution is an optional sensitivity analysis, disabled by default as in the original CLI.",
        normalize_non_parallel=normalize_non_parallel,
        points=points,
        task_means=task_means,
        parallel_normalization=replacements,
        published_coordinates=published,
        differences_from_paper=differences,
        inputs=fingerprint(files, root),
    )


def plot(data, out):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}
    )
    number = data["figure"]
    if number == 8:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        for s in data["series"]:
            for ax, key in zip(axes, ["k", "t"]):
                xy = s[key]
                label = MODELS[s["model"]][0] + (
                    " + protocol" if s["protocol"] else " − protocol"
                )
                ax.plot(
                    [x[0] for x in xy],
                    [x[1] for x in xy],
                    color=MODELS[s["model"]][1],
                    linestyle="-" if s["protocol"] else "--",
                    label=label,
                )
        for ax, x, y in zip(
            axes, ["Candidates (k)", "Generation time (s)"], ["Pass@k", "Pass@t"]
        ):
            ax.set(xlabel=x, ylabel=y, ylim=(0, 1.03))
            ax.grid(alpha=0.2)
        axes[1].legend(fontsize=7)
    else:
        fig, ax = plt.subplots(figsize=(8, 5))
        for p in data["points"]:
            label = (
                (
                    MODELS[p["model"]][0]
                    + (" + protocol" if p["protocol"] else " − protocol")
                )
                if number == 5
                else p["model"] + " / " + p["strategy"]
            )
            ax.scatter(
                p["latency_s"],
                p["accuracy"],
                label=label,
                color=MODELS[p["model"]][1]
                if number == 5
                else SCHEDULER_COLORS[p["model"]],
                marker=("o" if p["protocol"] else "x")
                if number == 5
                else {
                    "Serial": "^",
                    "Parallel": "s",
                    "Hedge": "o",
                    "JIT-Scheduler": "D",
                    "Oracle-Scheduler": "*",
                }[p["strategy"]],
            )
        ax.set(
            xlabel="Planning latency (s)" if number == 5 else "Latency (s)",
            ylabel="Valid-plan rate" if number == 5 else "Accuracy",
            ylim=(0, 1.03),
        )
        ax.grid(alpha=0.2)
        ax.legend(fontsize=6, ncol=2, loc="lower right")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out / f"figure{number}.{ext}", dpi=180)
    plt.close(fig)


def analyze(root, figure, output, normalize_non_parallel=False):
    output.mkdir(parents=True, exist_ok=True)
    from .additional_figures import figure6, figure7, figure9, plot_additional

    for number in [5, 6, 7, 8, 9, 10] if figure == "all" else [int(figure)]:
        data = (
            figure10(root, normalize_non_parallel=normalize_non_parallel)
            if number == 10
            else {5: figure5, 6: figure6, 7: figure7, 8: figure8, 9: figure9}[number](
                root
            )
        )
        data["artifact_schema_version"] = "1.0"
        data["kind"] = "recorded_analysis"
        (output / f"figure{number}.json").write_text(
            json.dumps(data, indent=2, allow_nan=False) + "\n"
        )
        if number in (6, 7, 9):
            plot_additional(data, output)
        else:
            plot(data, output)
    return {
        "output": str(output.resolve()),
        "figures": [5, 6, 7, 8, 9, 10] if figure == "all" else [int(figure)],
        "model_calls": 0,
    }
