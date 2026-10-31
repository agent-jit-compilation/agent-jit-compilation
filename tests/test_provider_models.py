"""Provider routing, output limits, and accounting without network access."""

import asyncio
import base64
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


def test_temperature_reaches_native_and_openai_clients(tmp_path, monkeypatch):
    pytest.importorskip("browser_use")
    from blastai.agents.llm_factory import LLMFactory

    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-not-a-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    temperatures = []

    def factory(name, **kwargs):
        temperatures.append(kwargs["temperature"])
        return SimpleNamespace(get_client=lambda: SimpleNamespace())

    monkeypatch.setattr(LLMFactory, "create_llm", factory)
    ledger = Ledger(tmp_path / "ledger.json", 10, 8)
    BudgetedModel("gemini-2.5-flash", ledger, 64)
    BudgetedModel("gemini-2.5-flash", ledger, 64, 0.5)
    assert temperatures == [0.0, 0.5]
    assert BudgetedModel("gpt-4.1", ledger, 64).temperature == 0.0
    assert BudgetedModel("gpt-4.1", ledger, 64, 0.5).temperature == 0.5


def fixture_png(width, height, padding=0):
    header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
    return header + width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes(9 + padding)


def test_image_size_reads_png_and_jpeg_headers():
    from agent_jit.native_model import image_size

    assert image_size(fixture_png(1280, 1100)) == (1280, 1100)
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + bytes(14)
    sof0 = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
    jpeg = b"\xff\xd8" + app0 + sof0 + (720).to_bytes(2, "big") + (1280).to_bytes(2, "big") + bytes(12)
    assert image_size(jpeg) == (1280, 720)
    assert image_size(b"not an image") is None


def test_screenshot_reservation_uses_image_tokens_not_base64_bytes(tmp_path, monkeypatch):
    pytest.importorskip("browser_use")
    from blastai.agents.llm_factory import LLMFactory
    from browser_use.llm.messages import (
        ContentPartImageParam,
        ContentPartTextParam,
        ImageURL,
        UserMessage,
    )
    from agent_jit.native_model import NativeModel, image_token_bound

    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-not-a-key")

    async def invoke(messages, output_format=None):
        return SimpleNamespace(completion="ok")

    monkeypatch.setattr(
        LLMFactory,
        "create_llm",
        lambda name, **kwargs: SimpleNamespace(get_client=lambda: SimpleNamespace(), ainvoke=invoke),
    )
    image = "data:image/png;base64," + base64.b64encode(fixture_png(1280, 1100, 1_000_000)).decode()
    # Gemini bills 258 tokens per 768x768 tile: a 1280x1100 screenshot is four tiles.
    assert image_token_bound(image, "google") == 2 * 258 * 4
    assert image_token_bound("data:image/png;base64,not-an-image", "google") > 0
    path = tmp_path / "ledger.json"
    llm = NativeModel("gemini-2.5-flash", Ledger(path, 10, 4), 64)
    message = UserMessage(
        content=[ContentPartTextParam(text="state"), ContentPartImageParam(image_url=ImageURL(url=image))]
    )
    asyncio.run(llm.ainvoke([message]))
    bound = json.loads(path.read_text())["requests"][0]["input_token_bound"]
    assert 2 * 258 * 4 < bound < 10_000 < len(image)
