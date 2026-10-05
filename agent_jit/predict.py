"""Bounded calls through the original scheduler's usage-prediction prompts."""

import contextlib
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace

from .budget import Ledger, RATES
from .model_catalog import provider, require_credentials
from .scheduler import estimate, resolve_inputs
from .analysis import fingerprint


class BudgetedResponses:
    def __init__(self, model, ledger, max_output_tokens=2048):
        from openai import OpenAI

        if model not in RATES:
            raise ValueError("Unsupported predictor model price bound")
        self.model = model
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens
        require_credentials(model)
        self.client = (
            None
            if provider(model) != "openai"
            else OpenAI(max_retries=0, timeout=60, base_url="https://api.openai.com/v1")
        )
        self.responses = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        if provider(self.model) != "openai":
            from .native_model import invoke_structured

            response = invoke_structured(
                self.model,
                self.ledger,
                kwargs["input"],
                kwargs["text_format"],
                self.max_output_tokens,
            )
            return SimpleNamespace(output_parsed=response.completion)
        if self.model.startswith("gpt-5"):
            kwargs.pop("temperature", None)
        kwargs["model"] = self.model
        kwargs["max_output_tokens"] = self.max_output_tokens
        payload = {
            **kwargs,
            "text_format": kwargs["text_format"].model_json_schema(),
            "messages": kwargs["input"],
        }
        rid = self.ledger.reserve(self.model, payload, self.max_output_tokens)
        try:
            response = self.client.responses.parse(**kwargs)
        except BaseException:
            self.ledger.finish(rid, status="request_failed_charge_unknown")
            raise
        usage = response.usage
        self.ledger.finish(
            rid,
            None
            if usage is None
            else {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
            status="complete" if usage else "complete_usage_unavailable",
        )
        if response.output_parsed is None:
            raise ValueError(
                "Scheduler response was incomplete or refused; no parsed prediction"
            )
        return response

    def close(self):
        if self.client is not None:
            self.client.close()


def predict(
    root,
    task,
    application,
    profile_model,
    predictor_model,
    mode,
    seed,
    trials,
    ledger_path,
    budget,
    output,
    max_output_tokens=2048,
    *,
    profile_path=None,
    schema_root=None,
    max_requests=128,
    overheads=None,
):
    from blast_scheduler.online.scheduler import Scheduler

    profile, schema_root, _ = resolve_inputs(
        root, application, profile_model, profile_path, schema_root, overheads
    )
    if mode not in ("mean", "win_rate") or not 1 <= trials <= 100000:
        raise ValueError("Invalid selection mode or Monte Carlo trial count")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    client = BudgetedResponses(
        predictor_model, Ledger(ledger_path, budget, max_requests), max_output_tokens
    )
    diagnostics = io.StringIO()
    try:
        with contextlib.redirect_stdout(diagnostics):
            planner = Scheduler(
                client,
                application,
                element_latencies_file=str(profile),
                schema_root=str(schema_root),
            )
            from blast_scheduler.offline.schema import list_defs

            schema_elements = set()
            for schema in planner.page_schemas.values():
                parsed = json.loads(schema) if isinstance(schema, str) else schema
                elements, modals = list_defs(parsed)
                schema_elements.update(name for name, _ in elements)
                schema_elements.update(
                    name for group in modals.values() for name, _ in group
                )
            coverage = {
                "schema_elements": sorted(schema_elements),
                "profiled_elements": sorted(planner.valid_elements),
                "unprofiled_elements": sorted(
                    schema_elements - set(planner.valid_elements)
                ),
            }
            if coverage["unprofiled_elements"]:
                import warnings

                warnings.warn(
                    f"Latency profile lacks {len(coverage['unprofiled_elements'])} schema elements; these cannot be selected by the usage predictor. See profile-coverage.json.",
                    stacklevel=2,
                )
            (output / "profile-coverage.json").write_text(
                json.dumps(coverage, indent=2) + "\n"
            )
            sequential = planner.get_sequential_usage(task)
            parallel = planner.get_parallel_usage(task, sequential)
        usage = {
            "sequential": sequential.model_dump(),
            "parallel": parallel.model_dump() if parallel else None,
        }
        usage_path = output / "usage.json"
        usage_path.write_text(json.dumps(usage, indent=2) + "\n")
        result = estimate(
            root,
            usage_path,
            application,
            profile_model,
            mode,
            seed,
            trials,
            profile_path=profile,
            schema_root=schema_root,
            overheads=overheads,
        )
        result.update(
            kind="online_schedule_prediction",
            task=task,
            predictor_model=predictor_model,
            profile_coverage=coverage,
            scheduling_s=time.perf_counter() - start,
            scope="Original element-usage prompts and Monte Carlo selector; browser execution is separate.",
            implementation_inputs=fingerprint(
                [
                    root / "blast_scheduler/online/scheduler.py",
                    root / "blast_scheduler/online/scheduler_cost_service.py",
                ],
                root,
            ),
        )
        (output / "prediction.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        (output / "prediction.log").write_text(diagnostics.getvalue())
        client.close()
