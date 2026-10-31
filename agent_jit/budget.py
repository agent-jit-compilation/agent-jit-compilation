"""Durable request reservations for supported model providers.

This ledger covers calls through this client, not other processes using the key.
Uncertain charges (timeouts/interrupted streams) retain their full reservation.
"""

import contextlib
import fcntl
import json
import math
from pathlib import Path
import time
import uuid
from types import SimpleNamespace

from .model_catalog import MODELS, RATES, provider, require_credentials


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, budget_usd: float, max_requests: int):
        if (
            not isinstance(budget_usd, (int, float))
            or not math.isfinite(budget_usd)
            or budget_usd <= 0
        ):
            raise ValueError("budget_usd must be a finite positive number")
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        self.path = path
        self.budget_usd = budget_usd
        self.max_requests = max_requests
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def transaction(self):
        # Lock a stable sidecar: replacing the JSON file must not invalidate the lock.
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            d = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else {"schema_version": "1.0", "requests": []}
            )
            if any("reservation_usd" not in r for r in d["requests"]):
                raise ValueError(
                    "Unrecognized existing ledger; refusing to reset spending history"
                )
            yield d
            d["schema_version"] = "1.0"
            d["accounted_usd"] = sum(
                r.get("estimated_cost_usd", r["reservation_usd"]) for r in d["requests"]
            )
            d["cost_basis"] = (
                "Provider token usage at uncached list rates when available; otherwise full conservative reservation. Estimates, not invoices."
            )
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(d, indent=2, allow_nan=False) + "\n")
            temp.replace(self.path)

    def reserve(self, model, payload, max_output_tokens):
        if model not in RATES:
            raise ValueError("No verified price bound for this model")
        if not 1 <= max_output_tokens <= 32768:
            raise ValueError("Invalid output-token limit")
        for msg in payload.get("messages", []):
            if not isinstance(msg.get("content"), str):
                raise ValueError("Bounded client accepts text messages only")
        # UTF-8 byte count is a conservative text-token bound; allowance covers chat framing.
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 4096
        input_rate, output_rate = RATES[model]
        amount = (input_bound * input_rate + max_output_tokens * output_rate) / 1e6
        denied = None
        with self.transaction() as d:
            ceiling = self.budget_usd
            d["budget_usd"] = max(ceiling, d.get("budget_usd", 0))
            used = sum(
                r.get("estimated_cost_usd", r["reservation_usd"]) for r in d["requests"]
            )
            if len(d["requests"]) >= self.max_requests or used + amount > ceiling:
                denied = {
                    "id": str(uuid.uuid4()),
                    "model": model,
                    "at_unix": time.time(),
                    "reason": "request_limit"
                    if len(d["requests"]) >= self.max_requests
                    else "dollar_limit",
                    "max_requests": self.max_requests,
                    "budget_usd": ceiling,
                }
                d.setdefault("denials", []).append(denied)
            else:
                rid = str(uuid.uuid4())
                d["requests"].append(
                    {
                        "id": rid,
                        "model": model,
                        "started_at_unix": time.time(),
                        "reservation_usd": amount,
                        "input_token_bound": input_bound,
                        "max_output_tokens": max_output_tokens,
                        "status": "reserved",
                        "price_source": MODELS[model][3],
                    }
                )
        if denied:
            raise BudgetExceeded(
                f"Request blocked by {denied['reason']}; increase the configured cumulative limit"
            )
        return rid

    def assert_available(self):
        """Reject an already exhausted campaign before launching another trial."""
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        spent = sum(
            r.get("estimated_cost_usd", r["reservation_usd"]) for r in data["requests"]
        )
        if len(data["requests"]) >= self.max_requests or spent >= self.budget_usd:
            raise BudgetExceeded(
                "Campaign ledger is exhausted; increase budget_usd/max_requests before starting another trial"
            )

    def finish(self, rid, usage=None, status="complete"):
        with self.transaction() as d:
            r = next(x for x in d["requests"] if x["id"] == rid)
            r["status"] = status
            if usage is not None:
                rates = RATES[r["model"]]
                r["usage"] = usage
                r["estimated_cost_usd"] = (
                    usage["prompt_tokens"] * rates[0]
                    + usage["completion_tokens"] * rates[1]
                ) / 1e6


class BudgetedModel:
    """Small duck-typed model used by the original streaming CodeGenerator."""

    provider = "openai"

    def __new__(cls, model, ledger, max_completion_tokens=1024, temperature=0.0):
        if provider(model) != "openai":
            from .native_model import NativeModel

            return NativeModel(model, ledger, max_completion_tokens, temperature)
        return super().__new__(cls)

    def __init__(self, model, ledger, max_completion_tokens=1024, temperature=0.0):
        from openai import AsyncOpenAI

        require_credentials(model)
        self.model = model
        self.ledger = ledger
        self.max_completion_tokens = max_completion_tokens
        self.temperature = temperature
        self.client = AsyncOpenAI(
            max_retries=0, timeout=60.0, base_url="https://api.openai.com/v1"
        )
        self.proxy = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        )

    def get_client(self):
        return self.proxy

    async def ainvoke(self, *args, **kwargs):
        raise RuntimeError(
            "No unbudgeted fallback: streaming request failed; see ledger"
        )

    async def create(self, **kwargs):
        if self.model.startswith("gpt-5"):
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)
            kwargs.setdefault("reasoning_effort", "low")
        kwargs["max_completion_tokens"] = self.max_completion_tokens
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        rid = self.ledger.reserve(self.model, kwargs, self.max_completion_tokens)
        try:
            stream = await self.client.chat.completions.create(**kwargs)

            async def tracked():
                usage = None
                try:
                    async for chunk in stream:
                        if chunk.usage:
                            usage = chunk.usage.model_dump()
                        yield chunk
                    self.ledger.finish(
                        rid,
                        usage,
                        status="complete" if usage else "complete_usage_unavailable",
                    )
                except BaseException:
                    self.ledger.finish(rid, usage, status="interrupted")
                    raise
                finally:
                    await stream.close()

            return tracked()
        except BaseException:
            self.ledger.finish(rid, status="request_failed_charge_unknown")
            raise
