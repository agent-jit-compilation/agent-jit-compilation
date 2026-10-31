"""Budgeted provider-native Browser-Use models for planning and browser execution."""

import base64
import inspect
import json
import math

from .model_catalog import provider, require_credentials


def image_size(data):
    """Pixel width and height of PNG or JPEG bytes, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(
                    data[i + 5 : i + 7], "big"
                )
            i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return None


def image_token_bound(url, provider_name):
    """Twice the provider's documented token count for an inline image.

    Falls back to the base64 byte count when the image size cannot be read.
    """
    try:
        size = image_size(base64.b64decode(url.partition(",")[2]))
    except ValueError:
        size = None
    if not size or not all(size):
        return len(url.encode())
    width, height = size
    if provider_name == "google":
        # Gemini 2.5: 258 tokens per 768x768 tile.
        tokens = 258 * math.ceil(width / 768) * math.ceil(height / 768)
    else:
        # Anthropic: width x height / 750 after scaling to at most 1568 px per side.
        scale = min(1.0, 1568 / max(width, height))
        tokens = math.ceil(width * scale * height * scale / 750)
    return 2 * tokens


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
        # Inline images are bounded by their pixel size, not their base64 text; remote media is rejected.
        serialized = [m.model_dump(mode="json") for m in messages]
        image_tokens = 0
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
                        image_tokens += image_token_bound(
                            part["image_url"]["url"], self.provider
                        )
                        part["image_url"]["url"] = "data:image/"
        # The ledger bounds input at one token per byte, so each image adds its token bound.
        payload = {
            "messages": [
                {
                    "content": json.dumps(serialized, ensure_ascii=False)
                    + "x" * image_tokens
                }
            ]
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
