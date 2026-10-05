"""Provider routing, output limits, and accounting without network access."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent_jit.budget import BudgetedModel, Ledger, BudgetExceeded
from agent_jit.model_catalog import require_credentials


@pytest.mark.parametrize(
    "model,key",
    [
        ("gemini-2.5-flash", "GOOGLE_API_KEY"),
        ("claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
        ("openai/gpt-oss-120b", "GROQ_API_KEY"),
    ],
)
def test_native_routing_and_accounting(tmp_path, monkeypatch, model, key):
    pytest.importorskip("browser_use")
    from blastai.agents.llm_factory import LLMFactory
    from browser_use.llm.messages import UserMessage
    from agent_jit.native_model import NativeModel

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(key, "fixture-not-a-key")
    options = []
    calls = []
    closed = []
    path = tmp_path / "ledger.json"

    async def close():
        closed.append(True)

    async def create(**kwargs):
        return None

    client = SimpleNamespace(
        close=close,
        aio=SimpleNamespace(aclose=close),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    async def invoke(messages, output_format=None):
        # Every request must be reserved before crossing the provider boundary.
        assert len(json.loads(path.read_text())["requests"]) == 1
        calls.append(messages)
        return SimpleNamespace(completion="print(1)")

    def factory(name, **kwargs):
        assert name == model
        options.append(kwargs)
        return SimpleNamespace(get_client=lambda: client, ainvoke=invoke)

    monkeypatch.setattr(LLMFactory, "create_llm", factory)
    llm = BudgetedModel(model, Ledger(path, 10, 1), 123)
    assert isinstance(llm, NativeModel)

    async def exercise():
        assert (
            await llm.ainvoke([UserMessage(content="hello")])
        ).completion == "print(1)"
        with pytest.raises(BudgetExceeded):
            await llm.ainvoke([UserMessage(content="second call")])
        await llm.close()

    asyncio.run(exercise())
    assert len(calls) == 1 and closed
    assert (
        json.loads(path.read_text())["requests"][0]["status"]
        == "complete_conservative_reservation"
    )
    if model.startswith("gemini"):
        assert options[0]["max_output_tokens"] == 123
        assert options[0]["http_options"]["retry_options"]["attempts"] == 1
    elif model.startswith("claude"):
        assert options[0]["max_tokens"] == 123 and options[0]["max_retries"] == 0
    else:
        seen = []

        async def wire(**kwargs):
            seen.append(kwargs)

        # Check the Groq-specific cap by reconstructing with the wire fixture.
        client.chat.completions.create = wire
        llm = NativeModel(model, Ledger(tmp_path / "groq.json", 10, 128), 123)
        asyncio.run(llm._chat.get_client().chat.completions.create(model=model))
        assert seen[0]["max_completion_tokens"] == 123
        asyncio.run(llm.close())


def test_gemini_key_alias_and_missing_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_API_KEY or GEMINI_API_KEY"):
        require_credentials("gemini-2.5-pro")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-not-a-key")
    require_credentials("gemini-2.5-pro")


def test_gpt5_browser_request_uses_reasoning_cap(tmp_path, monkeypatch):
    pytest.importorskip("browser_use")
    from agent_jit.browser_model import BrowserModel

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(usage=None)

    async def close():
        pass

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            close=close,
        ),
    )
    llm = BrowserModel("gpt-5", Ledger(tmp_path / "ledger.json", 10, 128), 2048)
    asyncio.run(
        llm.create(
            model="gpt-5",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.2,
            top_p=0.9,
        )
    )
    assert "temperature" not in seen[0] and "top_p" not in seen[0]
    assert seen[0]["max_completion_tokens"] == 2048
    assert seen[0]["reasoning_effort"] == "low"
