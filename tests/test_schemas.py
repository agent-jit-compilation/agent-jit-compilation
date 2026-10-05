"""Exercise original schema prompt generation with a local provider fixture."""

import json
from types import SimpleNamespace

import pytest

from agent_jit import schemas
from agent_jit.budget import BudgetExceeded


def test_schema_pipeline_preserves_prompt_and_accounts_for_images(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    images = tmp_path / "screenshots"
    images.mkdir()
    (images / "main.png").write_bytes(b"fixture image; never sent to a provider")
    config = tmp_path / "pages.json"
    config.write_text(
        json.dumps(
            [
                {
                    "url": "https://example.test/",
                    "image_folder": "screenshots",
                    "output_name": "main",
                },
                {
                    "url": "https://example.test/cart",
                    "image_folder": "screenshots",
                    "output_name": "cart",
                },
            ]
        )
    )
    calls = []
    closed = []

    def create(**kwargs):
        calls.append(kwargs)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {"Checkout": {"type": "object"}},
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(schema)),
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 50}
            ),
        )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            close=lambda: closed.append(True),
        ),
    )
    ledger = tmp_path / "ledger.json"
    output = tmp_path / "schemas"
    result = schemas.build_schemas(config, output, "gpt-4.1", ledger, 1)
    assert result["status"] == "success" and len(calls) == 2 and closed
    assert calls[0]["messages"][0]["content"][1]["type"] == "text"
    assert calls[0]["messages"][0]["content"][2]["type"] == "image_url"
    assert "Checkout" in calls[1]["messages"][0]["content"][0]["text"]
    assert json.loads((output / "config.json").read_text())["page_order"] == [
        "main",
        "cart",
    ]
    charges = json.loads(ledger.read_text())["requests"]
    assert len(charges) == 2 and all(r["input_token_bound"] > 4096 for r in charges)
    with pytest.raises(ValueError, match="already exists"):
        schemas.build_schemas(config, output, "gpt-4.1", ledger, 1)

    with pytest.raises(BudgetExceeded):
        schemas.build_schemas(
            config, tmp_path / "denied", "gpt-4.1", tmp_path / "tiny.json", 0.001
        )
    assert len(calls) == 2
    assert (
        json.loads((tmp_path / "denied/build.json").read_text())["status"] == "failed"
    )
    assert not (tmp_path / "denied/config.json").exists()


def test_schema_inputs_and_output_contract_fail_closed(tmp_path):
    config = tmp_path / "pages.json"
    config.write_text(
        json.dumps([{"url": "https://example.test", "output_name": "../escape"}])
    )
    with pytest.raises(ValueError, match="plain page name"):
        schemas.load_pages(config)
    generator = schemas.ValidatedGenerator(client=object())
    with pytest.raises(ValueError, match="actionable elements"):
        generator.save_schema({"type": "object"}, str(tmp_path / "empty.json"))
    assert not (tmp_path / "empty.json").exists()


def test_custom_schema_prompt_validates_before_provider_call(tmp_path, monkeypatch):
    from blast_scheduler.page_schema.prompts import SCHEMA_PROMPT

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    images = tmp_path / "images"
    images.mkdir()
    (images / "page.png").write_bytes(b"fixture")
    config = tmp_path / "pages.json"
    config.write_text(
        json.dumps(
            [
                {
                    "url": "https://fixture.test/",
                    "image_folder": "images",
                    "output_name": "main",
                }
            ]
        )
    )
    prompt = tmp_path / "custom.txt"
    prompt.write_text(SCHEMA_PROMPT + "\nUse product terminology.")
    seen = []

    class Client:
        def __init__(self, *args):
            pass

        def close(self):
            pass

    def generate(self, pages, output):
        from blast_scheduler.page_schema.generator import SCHEMA_PROMPT as active

        seen.append(active)

    monkeypatch.setattr(schemas, "SchemaClient", Client)
    monkeypatch.setattr(schemas.ValidatedGenerator, "generate_site_schemas", generate)
    result = schemas.build_schemas(
        config,
        tmp_path / "out",
        "gpt-4.1",
        tmp_path / "ledger.json",
        1,
        prompt_file=prompt,
    )
    assert result["status"] == "success" and seen == [prompt.read_text()]
    assert (tmp_path / "out/schema-prompt.txt").read_text() == prompt.read_text()
    from blast_scheduler.page_schema.generator import SCHEMA_PROMPT as restored

    assert restored == SCHEMA_PROMPT
    prompt.write_text("Forgot the input placeholders")
    with pytest.raises(ValueError, match="placeholders"):
        schemas.build_schemas(
            config,
            tmp_path / "invalid",
            "gpt-4.1",
            tmp_path / "ledger.json",
            1,
            prompt_file=prompt,
        )
    assert not (tmp_path / "invalid").exists()
