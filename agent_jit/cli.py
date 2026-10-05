"""Commands for planning, scheduling, evaluation, and data preparation."""

import argparse
import json
import sys
from pathlib import Path
from .budget import RATES

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="agent-jit",
        description="Agent JIT Compilation: web-agent planning and scheduling",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=ROOT,
        help="Checkout containing data/ and benchmarks/",
    )
    subs = parser.add_subparsers(dest="command", required=True)
    analyze = subs.add_parser(
        "analyze", help="Regenerate paper figures from recorded outputs; no API calls"
    )
    analyze.add_argument(
        "--figure", choices=["5", "6", "7", "8", "9", "10", "all"], default="all"
    )
    analyze.add_argument(
        "--normalize-non-parallel",
        action="store_true",
        help="Figure 10 sensitivity analysis: substitute serial trials when a parallel run completes one task",
    )
    analyze.add_argument("--output", type=Path, default=Path("runs/recorded"))
    planner_analysis = subs.add_parser(
        "analyze-planner",
        help="Recompute recorded planner E2E summaries as CSV/JSON; no API calls",
    )
    planner_analysis.add_argument("--output", type=Path, default=Path("runs/recorded"))
    plan = subs.add_parser(
        "planner",
        help="Validate and rank candidate Python plans without executing them",
    )
    plan.add_argument("action", choices=["check", "rank"])
    plan.add_argument("plans", type=Path, nargs="+")
    plan.add_argument("--registry", type=Path, required=True)
    plan.add_argument("--state", default="{}", help="Initial abstract state as JSON")
    schedule = subs.add_parser(
        "scheduler",
        help="Estimate schedules using original Monte Carlo cost model; no API calls",
    )
    schedule.add_argument(
        "usage", type=Path, help="JSON with sequential and optional parallel usage"
    )
    schedule.add_argument(
        "--application", required=True, help="Application schema directory name"
    )
    schedule.add_argument(
        "--model",
        help="Latency-profile model name; custom names require --overheads",
        required=True,
    )
    schedule.add_argument(
        "--mode",
        choices=["mean", "win_rate"],
        required=True,
        help="Minimize expected latency (mean) or maximize probability of finishing first (win_rate)",
    )
    schedule.add_argument("--seed", type=int, default=0)
    schedule.add_argument("--trials", type=int, default=1000)
    schedule.add_argument("--latency-profile", type=Path)
    schedule.add_argument("--schema-root", type=Path)
    schedule.add_argument(
        "--overheads",
        type=json.loads,
        help="JSON seconds: hedge, parallel, page_read, repeat_interaction",
    )
    gen = subs.add_parser(
        "generate",
        help="Generate and rank plans using the original planner (paid, budget required)",
    )
    gen.add_argument("--task", required=True)
    gen.add_argument("--registry", type=Path, required=True)
    gen.add_argument("--state", required=True, help="Initial abstract state as JSON")
    gen.add_argument(
        "--model",
        choices=list(RATES),
        default="gpt-4.1-2025-04-14",
    )
    gen.add_argument(
        "--candidates", type=int, default=1, help="Target number of candidates"
    )
    gen.add_argument(
        "--candidate-target",
        choices=["valid", "sampled"],
        default="valid",
        help="Collect valid plans or a fixed number of attempts",
    )
    gen.add_argument(
        "--max-candidate-attempts",
        type=int,
        help="Generation attempts before stopping; default 4 times the valid target",
    )
    gen.add_argument(
        "--max-requests",
        type=int,
        default=128,
        help="Cumulative request ceiling for the shared ledger",
    )
    gen.add_argument("--max-output-tokens", type=int, default=1024)
    gen.add_argument("--budget-usd", type=float, required=True)
    gen.add_argument(
        "--ledger",
        type=Path,
        required=True,
        help="Cumulative spending ledger shared across related runs",
    )
    gen.add_argument("--output", type=Path, required=True)
    evaluate = subs.add_parser(
        "evaluate",
        help="Run isolated REAL or WebArena trials through a shared adapter and evaluator",
    )
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    validate = subs.add_parser(
        "validate-results",
        help="Validate trial JSONL and report scored accuracy and campaign completeness",
    )
    validate.add_argument("path", type=Path)
    catalog = subs.add_parser(
        "tasks", help="List the 37 paper tasks or inspect one task"
    )
    catalog.add_argument("task_id", nargs="?")
    catalog.add_argument(
        "--catalog", type=Path, help="Inspect a task from a custom task catalog"
    )
    catalog.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON"
    )
    catalog.add_argument(
        "--component",
        choices=["planner", "scheduler"],
        help="List only the tasks evaluated with this component in the paper",
    )
    wa = subs.add_parser(
        "prepare-webarena",
        help="Fetch pinned evaluator and apply checksummed saved overrides",
    )
    wa.add_argument("--output", type=Path, default=Path(".downloads/webarena"))
    predict = subs.add_parser(
        "predict-schedule",
        help="Predict usage and select a schedule with the original prompts (paid, budget required)",
    )
    predict.add_argument("--task", required=True)
    predict.add_argument(
        "--application", required=True, help="Application schema directory name"
    )
    predict.add_argument(
        "--profile-model",
        help="Latency-profile model name; custom names require --overheads",
        required=True,
    )
    predict.add_argument(
        "--predictor-model",
        choices=list(RATES),
        default="gpt-4.1-2025-04-14",
    )
    predict.add_argument("--mode", choices=["mean", "win_rate"], required=True)
    predict.add_argument("--seed", type=int, default=0)
    predict.add_argument("--trials", type=int, default=1000)
    predict.add_argument("--ledger", type=Path, required=True)
    predict.add_argument("--budget-usd", type=float, required=True)
    predict.add_argument("--output", type=Path, required=True)
    predict.add_argument("--latency-profile", type=Path)
    predict.add_argument("--schema-root", type=Path)
    predict.add_argument(
        "--overheads",
        type=json.loads,
        help="JSON seconds: hedge, parallel, page_read, repeat_interaction",
    )
    predict.add_argument("--max-requests", type=int, default=128)
    synthesis = subs.add_parser(
        "synthesize-tools",
        help="Create candidate browser tools with the original synthesis agent (paid offline setup)",
    )
    synthesis.add_argument("--task", required=True)
    synthesis.add_argument("--url", required=True)
    synthesis.add_argument("--registry", type=Path)
    synthesis.add_argument("--output", type=Path, required=True)
    synthesis.add_argument(
        "--model",
        choices=list(RATES),
        default="gpt-4.1-2025-04-14",
    )
    synthesis.add_argument("--ledger", type=Path, required=True)
    synthesis.add_argument("--budget-usd", type=float, required=True)
    synthesis.add_argument("--timeout-s", type=int, default=180)
    parse_trace = subs.add_parser(
        "parse-traces", help="Extract steps from raw Laminar exports; no API calls"
    )
    parse_trace.add_argument("--input-dir", type=Path, required=True)
    parse_trace.add_argument("--output", type=Path, required=True)
    parse_trace.add_argument(
        "--format",
        choices=["auto", "chat-spans", "agent-run", "browser-history"],
        default="auto",
    )
    label_trace = subs.add_parser(
        "label-traces", help="Map parsed steps to page elements (paid offline setup)"
    )
    label_trace.add_argument("--trace-steps", type=Path, required=True)
    label_trace.add_argument("--schema-root", type=Path, required=True)
    label_trace.add_argument("--application", required=True)
    label_trace.add_argument("--output", type=Path, required=True)
    label_trace.add_argument(
        "--model",
        choices=list(RATES),
        default="gpt-4.1-2025-04-14",
    )
    label_trace.add_argument("--ledger", type=Path, required=True)
    label_trace.add_argument("--budget-usd", type=float, required=True)
    label_trace.add_argument("--max-retries", type=int, default=10)
    label_trace.add_argument(
        "--max-requests",
        type=int,
        default=1024,
        help="Cumulative request ceiling for the shared ledger",
    )
    profile = subs.add_parser(
        "build-profile",
        help="Fit scheduler distributions from labeled trace steps; no API calls",
    )
    profile.add_argument(
        "--site", type=Path, required=True, help="site_rep.json from label-traces"
    )
    profile.add_argument(
        "--trace-steps",
        type=Path,
        required=True,
        help="Directory of parsed timed steps",
    )
    profile.add_argument(
        "--output", type=Path, required=True, help="New directory for fitted profiles"
    )
    profile.add_argument(
        "--execution-model", required=True, help="Model that produced the timed traces"
    )
    schemas = subs.add_parser(
        "build-schemas",
        help="Extract page schemas from screenshots (paid offline setup)",
    )
    schemas.add_argument("--config", type=Path, required=True)
    schemas.add_argument("--output", type=Path, required=True)
    schemas.add_argument(
        "--model",
        choices=list(RATES),
        default="gpt-4.1-2025-04-14",
    )
    schemas.add_argument("--ledger", type=Path, required=True)
    schemas.add_argument("--budget-usd", type=float, required=True)
    schemas.add_argument("--max-output-tokens", type=int, default=8192)
    schemas.add_argument(
        "--max-requests",
        type=int,
        default=128,
        help="Cumulative request ceiling for the shared ledger",
    )
    schemas.add_argument(
        "--prompt-file",
        type=Path,
        help="Replacement schema extraction template; copy SCHEMA_PROMPT and preserve its placeholders",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "synthesize-tools":
            import asyncio
            from agent_jit.synthesis import synthesize_tools

            result = asyncio.run(
                synthesize_tools(
                    args.task,
                    args.url,
                    args.output,
                    args.model,
                    args.ledger,
                    args.budget_usd,
                    args.registry,
                    args.timeout_s,
                )
            )
        elif args.command == "parse-traces":
            from agent_jit.traces import parse_traces

            result = parse_traces(args.input_dir, args.output, args.format)
        elif args.command == "label-traces":
            from agent_jit.traces import label_traces

            result = label_traces(
                args.trace_steps,
                args.schema_root,
                args.application,
                args.output,
                args.model,
                args.ledger,
                args.budget_usd,
                args.max_retries,
                max_requests=args.max_requests,
            )
        elif args.command == "build-profile":
            from agent_jit.profiles import build_profile

            result = build_profile(
                args.site, args.trace_steps, args.output, args.execution_model
            )
        elif args.command == "build-schemas":
            from agent_jit.schemas import build_schemas

            result = build_schemas(
                args.config,
                args.output,
                args.model,
                args.ledger,
                args.budget_usd,
                args.max_output_tokens,
                prompt_file=args.prompt_file,
                max_requests=args.max_requests,
            )
        elif args.command == "predict-schedule":
            from .predict import predict as run_prediction

            result = run_prediction(
                args.artifact_root,
                args.task,
                args.application,
                args.profile_model,
                args.predictor_model,
                args.mode,
                args.seed,
                args.trials,
                args.ledger,
                args.budget_usd,
                args.output,
                profile_path=args.latency_profile,
                schema_root=args.schema_root,
                overheads=args.overheads,
                max_requests=args.max_requests,
            )
        elif args.command == "prepare-webarena":
            from .webarena import prepare

            result = prepare(args.artifact_root, args.output)
        elif args.command == "analyze":
            from .analysis import analyze as run

            result = run(
                args.artifact_root,
                args.figure,
                args.output,
                args.normalize_non_parallel,
            )
        elif args.command == "analyze-planner":
            from .planner_results import analyze_planner

            result = analyze_planner(args.artifact_root, args.output)
        elif args.command == "evaluate":
            from .runner import evaluate_config

            result = evaluate_config(args.artifact_root, args.config, args.output)
        elif args.command == "validate-results":
            from .runner import validate_results

            result = validate_results(args.path)
        elif args.command == "tasks":
            from .catalog import task, tasks

            if args.catalog and not args.task_id:
                raise ValueError(
                    "Use tasks <id> --catalog <path> to inspect a custom task"
                )
            result = (
                task(args.artifact_root, args.task_id, args.catalog)
                if args.task_id
                else [
                    {
                        "id": t["id"],
                        "implementation_id": t["implementation_id"],
                        "application": t["application"],
                        "benchmark": t["benchmark"],
                        "task_details": t["task_details"],
                        "is_added": t["is_added"],
                        "evaluated_components": t["evaluated_components"],
                        "oracle_schedule": t.get("oracle_schedule"),
                    }
                    for t in tasks(args.artifact_root)
                    if not args.component or args.component in t["evaluated_components"]
                ]
            )
            if (
                args.task_id
                and args.component
                and args.component not in result.get("evaluated_components", [])
            ):
                raise ValueError(
                    f"Task {args.task_id} is not in the {args.component} task set"
                )
        elif args.command == "generate":
            import asyncio
            from .generate import generate

            result = asyncio.run(
                generate(
                    args.task,
                    args.registry,
                    json.loads(args.state),
                    args.model,
                    args.candidates,
                    args.output,
                    args.ledger,
                    args.budget_usd,
                    args.max_output_tokens,
                    args.candidate_target,
                    args.max_candidate_attempts,
                    args.max_requests,
                )
            )
        elif args.command == "scheduler":
            from .scheduler import estimate

            result = estimate(
                args.artifact_root,
                args.usage,
                args.application,
                args.model,
                args.mode,
                args.seed,
                args.trials,
                profile_path=args.latency_profile,
                schema_root=args.schema_root,
                overheads=args.overheads,
            )
        elif args.command == "planner":
            from .planner import rank

            state = json.loads(args.state)
            if not isinstance(state, dict):
                raise ValueError("--state must be a JSON object")
            result = rank(args.plans, args.registry, state)
        if args.command == "tasks" and not args.json:
            if args.task_id:
                print(
                    f"{result['id']} ({result['application']})\n{result['task_details']}\nURL: {result['initial_url']}\nVerifier: {result['verifier']}"
                )
            else:
                print(
                    "ID                     Application  Components          Instruction"
                )
                for item in result:
                    print(
                        f"{item['id']:<22} {item['application']:<12} {','.join(item['evaluated_components']):<19} {item['task_details']}"
                    )
        else:
            print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "planner" and (
            result["selected"] is None
            or (
                args.action == "check"
                and not all(c["valid"] for c in result["candidates"])
            )
        ):
            return 1
        if args.command == "generate" and result["selected_candidate"] is None:
            return 1
        if args.command == "synthesize-tools" and result["status"] != "generated":
            return 1
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
