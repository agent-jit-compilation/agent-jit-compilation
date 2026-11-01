"""Validate run inputs before starting browsers or recording trials."""

import math
from pathlib import Path

from .budget import Ledger, RATES
from .model_catalog import require_credentials, provider, cross_model_profile


def positive_int(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def validate_config(config, tasks, root):
    """Reject configuration errors before any benchmark trial starts."""
    adapter = config["adapter"]
    from .evaluation import custom_verifier

    for task in tasks:
        if task.get("verifier", {}).get("type") == "custom":
            custom_verifier(task["verifier"], root)
    paid = (
        adapter
        in {
            "agent_jit.adapters.planner:run",
            "agent_jit.adapters.browser_use:run",
            "agent_jit.adapters.scheduler:run",
            "agent_jit.adapters.claude_code:run",
        }
        or config["evaluator"] in ("openai", "historical")
        or config.get("webarena", {}).get("llm_judge", False)
    )
    for name in ("setup_timeout_s", "evaluation_timeout_s"):
        if name in config and (
            not isinstance(config[name], (int, float))
            or not math.isfinite(config[name])
            or config[name] <= 0
        ):
            raise ValueError(f"{name} must be a finite positive number")
    if type(config.get("webarena", {}).get("llm_judge", False)) is not bool:
        raise ValueError("webarena.llm_judge must be a boolean")
    if paid:
        if not config.get("ledger") or "budget_usd" not in config:
            raise ValueError("Model calls require ledger and budget_usd")
        Ledger(
            Path(config["ledger"]),
            config["budget_usd"],
            config.get("max_requests", 128),
        )
        # Claude Code calls and prices its own model, so only the answer parser is checked here.
        models = [] if "claude_code" in adapter else [config.get("model")]
        if "scheduler" in adapter:
            models.append(config.get("mini_model", config.get("model")))
            if config.get("strategy", "jit") == "jit":
                models.append(config.get("predictor_model", config.get("model")))
        if config["evaluator"] == "historical":
            models.append("claude-sonnet-4-20250514")
        if config.get("webarena", {}).get("llm_judge", False):
            models.append("gpt-4.1-mini")
        if config["evaluator"] == "openai":
            parser_model = config.get("parser_model", "gpt-4.1-2025-04-14")
            if provider(parser_model) != "openai":
                raise ValueError(
                    "evaluator: openai requires an OpenAI parser_model; use historical for the original Claude parser"
                )
            models.append(parser_model)
        for model in models:
            if model is not None and model not in RATES:
                raise ValueError(
                    f"Unsupported live model: {model}. Supported models: {', '.join(RATES)}"
                )
        if config.get("model") is None and "example" not in adapter:
            raise ValueError("A live model is required")
        for model in models:
            if model is not None:
                require_credentials(model)
        tokens = config.get("max_output_tokens", 2048)
        positive_int(tokens, "max_output_tokens")
        if tokens > 32768:
            raise ValueError("max_output_tokens must be at most 32768")
    for key in ("tool_registry", "latency_profile", "schema_root"):
        if config.get(key) and not Path(config[key]).exists():
            raise ValueError(f"{key} does not exist: {config[key]}")
    if adapter == "agent_jit.adapters.claude_code:run":
        import shutil
        from .adapters.claude_code import SKILL

        if not shutil.which("claude"):
            raise ValueError("The Claude Code adapter needs the claude command on PATH")
        if not (SKILL / "SKILL.md").is_file():
            raise ValueError(f"The Claude Code adapter needs the agent-browser skill at {SKILL}")
        budget = config.get("run_budget_usd")
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 20:
            raise ValueError("run_budget_usd must be a number between 0 and 20")
    if adapter == "agent_jit.adapters.planner:run":
        from .sampling import (
            CANDIDATE_TEMPERATURE,
            validate_sampling,
            validate_temperature,
        )

        validate_sampling(
            config.get("candidates", 1),
            config.get("candidate_target", "valid"),
            config.get("max_candidate_attempts"),
        )
        validate_temperature(config.get("candidate_temperature", CANDIDATE_TEMPERATURE))
        if config.get("selection", "best") not in ("best", "worst"):
            raise ValueError("selection must be best or worst")
        replay = config.get("replay_candidates", {})
        if not isinstance(replay, dict):
            raise ValueError("replay_candidates must map task IDs to earlier trial directories")
        for task_id, directory in replay.items():
            if task_id not in {task["id"] for task in tasks}:
                raise ValueError(f"replay_candidates names a task not in tasks: {task_id}")
            for name in ("candidates.json", "response.json"):
                if not (Path(directory) / name).is_file():
                    raise ValueError(f"replay_candidates: {directory} has no {name}")
        for task in tasks:
            registry = Path(
                config.get("tool_registry")
                or root / f"data/registries/{task['application']}.json"
            )
            if not registry.is_file():
                raise ValueError(f"Tool registry does not exist: {registry}")
    if adapter == "agent_jit.adapters.scheduler:run":
        if provider(config["model"]) == "groq" and config.get("allow_vision", True):
            raise ValueError(
                "GPT-OSS is text only; set allow_vision: false for scheduler execution"
            )
        from .scheduler import resolve_inputs

        strategy = config.get("strategy", "jit")
        if strategy not in (
            "jit",
            "serial",
            "parallel",
            "hedge",
            "sequential",
            "first_of_n",
        ):
            raise ValueError("strategy must be jit, serial, parallel, or hedge")
        if config.get("worker_budget", 4) != 4:
            raise ValueError("The scheduler supports a four-worker budget")
        if any(t["benchmark"] not in ("REAL", "custom") for t in tasks):
            raise ValueError("Scheduler execution requires REAL or custom tasks")
        if strategy == "jit":
            execution = config["model"]
            profile = config.get("profile_model", "gpt-4.1")
            if cross_model_profile(config):
                import warnings

                warnings.warn(
                    f"Execution model {execution} differs from latency profile {profile}; selection uses the profile's measurements. Supply a matching profile or record this as a cross-model experiment.",
                    stacklevel=2,
                )
            if config.get("selection_mode") not in ("mean", "win_rate"):
                raise ValueError("selection_mode must be mean or win_rate")
            n = config.get("monte_carlo_trials", 1000)
            positive_int(n, "monte_carlo_trials")
            if n > 100000:
                raise ValueError("monte_carlo_trials must be at most 100000")
            for task in tasks:
                resolve_inputs(
                    root,
                    task["application"],
                    config.get("profile_model", "gpt-4.1"),
                    config.get("latency_profile"),
                    config.get("schema_root"),
                    config.get("overheads_s"),
                )
    if "max_actions_per_step" in config:
        n = config["max_actions_per_step"]
        positive_int(n, "max_actions_per_step")
        if n > 10:
            raise ValueError("max_actions_per_step must be at most 10")
