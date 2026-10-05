"""Budgeted provider-native Browser-Use models for planning and browser execution."""

import inspect
import json

from .model_catalog import provider, require_credentials


class NativeModel:
    """Preserve provider serialization and structured-output handling.

    No get_client is exposed: the historical streaming helper uses its native
    ainvoke fallback for these providers, as it did for Google and Anthropic.
    Reservations are retained because provider-normalized usage can omit thinking
    or cache-write tokens. These are conservative estimates, not billed usage.
    """

    _verified_api_keys = False

    def __init__(self, model, ledger, max_output_tokens=2048, temperature=0.0):
        from blastai.agents.llm_factory import LLMFactory

        require_credentials(model)
        self.model = self.name = self.model_name = model
        self.provider = provider(model)
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens
        options = {}
        if self.provider == "google":
            options.update(
                max_output_tokens=max_output_tokens,
                http_options={"timeout": 60000, "retry_options": {"attempts": 1}},
            )
        elif self.provider == "anthropic":
            options.update(max_tokens=max_output_tokens, max_retries=0, timeout=60)
        elif self.provider == "groq":
            options.update(max_retries=0, timeout=60)
        else:
            raise ValueError("Use the OpenAI adapter for OpenAI models")
        self._chat = LLMFactory.create_llm(model, temperature=temperature, **options)
        self._client = self._chat.get_client()
        if self.provider == "groq":
            # The pinned ChatGroq does not forward a completion-token limit.
            from types import SimpleNamespace

            original = self._client.chat.completions.create

            async def bounded_create(**kwargs):
                kwargs["max_completion_tokens"] = max_output_tokens
                return await original(**kwargs)

            proxy = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=bounded_create))
            )
            self._chat.get_client = lambda: proxy
        else:
            self._chat.get_client = lambda: self._client
        self.client = self  # Existing planner cleanup calls llm.client.close().
        self.chat = self

    async def ainvoke(self, messages, output_format=None):
        # Serializing inline image bytes is conservative; remote media is rejected.
        serialized = [m.model_dump(mode="json") for m in messages]
        for message in serialized:
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        if self.provider == "groq":
                            raise ValueError(
                                "GPT-OSS accepts text only; disable vision (scheduler: allow_vision: false)"
                            )
                        if not part["image_url"]["url"].startswith("data:image/"):
                            raise ValueError(
                                "Use inline screenshots for budgeted requests"
                            )
        payload = {
            "messages": [{"content": json.dumps(serialized, ensure_ascii=False)}]
        }
        if output_format:
            payload["schema"] = output_format.model_json_schema()
        if self.provider == "anthropic":
            # Account for the native serializer's five-minute prompt-cache writes.
            encoded = payload["messages"][0]["content"]
            payload["messages"][0]["content"] += "x" * (
                (len(encoded.encode()) + 3) // 4
            )
        rid = self.ledger.reserve(self.model, payload, self.max_output_tokens)
        try:
            response = await self._chat.ainvoke(messages, output_format=output_format)
        except BaseException:
            self.ledger.finish(rid, status="request_failed_charge_unknown")
            raise
        self.ledger.finish(rid, status="complete_conservative_reservation")
        return response

    async def close(self):
        if self.provider == "google":
            await self._client.aio.aclose()
        result = self._client.close()
        if inspect.isawaitable(result):
            await result


def invoke_structured(model, ledger, messages, output_format, max_output_tokens=2048):
    """Synchronous bridge used by the original scheduler's Responses interface."""
    import asyncio
    from browser_use.llm.messages import UserMessage, SystemMessage, AssistantMessage

    classes = {
        "user": UserMessage,
        "system": SystemMessage,
        "assistant": AssistantMessage,
    }

    async def invoke():
        client = NativeModel(model, ledger, max_output_tokens)
        try:
            return await client.ainvoke(
                [classes[m["role"]].model_validate(m) for m in messages], output_format
            )
        finally:
            await client.close()

    return asyncio.run(invoke())
