"""Budget adapter for the pinned Browser-Use OpenAI chat model.

GPT-4.1 vision inputs fit within 2048² pixels and use 85 + 170/tile tokens.
Reserve 4096 tokens per screenshot (above the 16-tile upper bound), plus the
existing conservative text/framing bound. Mini uses a separate 12288-token
bound for patch-based vision. See docs/SCHEDULER.md for the sources.
"""

import asyncio
import copy
from types import SimpleNamespace
from .budget import RATES
from .model_catalog import provider


def bounded_payload(kwargs):
    if kwargs.get("n", 1) != 1 or kwargs.get("stream", False):
        raise ValueError("Only one non-streamed completion per browser request")
    model = kwargs.get("model")
    if model not in RATES:
        raise ValueError("Unsupported browser model price bound")
    image_bound = 12288 if "mini" in model or model.startswith("gpt-5") else 4096
    payload = copy.deepcopy(kwargs)
    for message in payload["messages"]:
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise ValueError("Unsupported browser message content")
        text = []
        for part in content:
            if part["type"] == "text":
                text.append(part["text"])
            elif part["type"] == "image_url":
                image = part["image_url"]
                if not image["url"].startswith("data:image/") or image.get(
                    "detail", "auto"
                ) not in ("low", "high", "auto"):
                    raise ValueError(
                        "Only inline low/high/auto screenshots have a price bound"
                    )
                text.append("x" * image_bound)
            else:
                raise ValueError("Unsupported browser multimodal input")
        message["content"] = "\n".join(text)
    return payload


class BrowserModel:
    def __new__(cls, model, ledger, max_output_tokens=2048, temperature=0.2):
        if provider(model) != "openai":
            from .native_model import NativeModel

            return NativeModel(model, ledger, max_output_tokens, temperature)
        return super().__new__(cls)

    def __init__(self, model, ledger, max_output_tokens=2048, temperature=0.2):
        from openai import AsyncOpenAI
        from browser_use.llm.openai.chat import ChatOpenAI

        if model not in RATES:
            raise ValueError("Unsupported browser model price bound")
        self.model = model
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens
        self.client = AsyncOpenAI(
            max_retries=0, timeout=60, base_url="https://api.openai.com/v1"
        )
        self.proxy = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        )
        # Preserve Browser-Use's own serializer, schema optimizer, and response parser.
        self.chat = ChatOpenAI(
            model=model,
            max_retries=0,
            max_completion_tokens=max_output_tokens,
            service_tier="default",
            temperature=temperature,
        )
        self.chat.get_client = lambda: self.proxy

    async def create(self, **kwargs):
        if kwargs.get("model") != self.model:
            raise ValueError("Browser request model differs from its budgeted model")
        if self.model.startswith("gpt-5"):
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)
            kwargs.setdefault("reasoning_effort", "low")
        kwargs["max_completion_tokens"] = self.max_output_tokens
        kwargs["service_tier"] = "default"
        rid = self.ledger.reserve(
            self.model, bounded_payload(kwargs), self.max_output_tokens
        )
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except BaseException:
            self.ledger.finish(rid, status="request_failed_charge_unknown")
            raise
        self.ledger.finish(
            rid,
            response.usage.model_dump() if response.usage else None,
            status="complete" if response.usage else "complete_usage_unavailable",
        )
        return response

    async def close(self):
        await self.client.close()


class BrowserModelPool:
    """Allow only explicitly configured models; share one ledger across all clients."""

    def __init__(self, models, ledger, max_output_tokens=2048):
        self.allowed = set(models)
        if not self.allowed or not self.allowed <= RATES.keys():
            raise ValueError("Unsupported configured browser model")
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens
        self.clients = {}

    def factory(self, name, **kwargs):
        model = name.removeprefix("openai:")
        if model not in self.allowed or kwargs:
            raise ValueError("Unbudgeted model or model options requested by BLAST")
        if model not in self.clients:
            self.clients[model] = BrowserModel(
                model, self.ledger, self.max_output_tokens
            )
        return self.clients[model].chat

    async def close(self):
        results = await asyncio.gather(
            *(client.close() for client in self.clients.values()),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            raise RuntimeError("Failed to close a browser model client") from failures[
                0
            ]
