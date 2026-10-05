"""Verify model routing and cleanup around the original synthesis agent."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("browser_use")

from agent_jit import synthesis
from blastai.agents import executor as executor_module
from blastai.agents.models import Agent

ROOT = Path(__file__).resolve().parents[1]


def test_synthesis_routes_helpers_and_retains_partial_tools_on_failure(
    tmp_path, monkeypatch
):
    events = []
    original_model = os.environ.get("BLASTAI_MODEL")
    fixture_tool = Agent.from_smcp_registry(
        str(ROOT / "data/registries/dashdish.json")
    ).tools[0]

    class Model:
        chat = object()

        def __init__(self, *args):
            pass

        async def close(self):
            events.append("model_closed")

    class Executor:
        def __init__(self, agent, **kwargs):
            self.agent = agent

        async def run(self, task, **kwargs):
            from blastai.agents.llm_factory import LLMFactory

            names = {tool.name for tool in self.agent.tools}
            assert {
                "ask_html",
                "update_smcp_tool",
                "remove_smcp_tool",
                "list_smcp_tools",
            } <= names
            assert (
                LLMFactory.create_llm(model_name="gpt-4.1", provider="openai")
                is Model.chat
            )
            with pytest.raises(ValueError, match="Unbudgeted"):
                LLMFactory.create_llm(model_name="another-model", provider="openai")
            self.agent.add_tool(fixture_tool)
            raise TimeoutError("fixture synthesis timeout")

        async def cleanup(self):
            events.append("executor_closed")

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path="fixture"))

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(synthesis, "BrowserModel", Model)
    monkeypatch.setattr("browser_use.Browser", lambda **kwargs: object())
    monkeypatch.setattr("patchright.async_api.async_playwright", Playwright)
    monkeypatch.setattr(executor_module, "AgentExecutor", Executor)
    output = tmp_path / "synthesis"
    with pytest.raises(TimeoutError, match="fixture synthesis timeout"):
        asyncio.run(
            synthesis.synthesize_tools(
                "Create a read-only tool",
                "https://example.test/",
                output,
                "gpt-4.1",
                tmp_path / "ledger.json",
                1,
            )
        )
    assert events == ["executor_closed", "model_closed"]
    report = json.loads((output / "build.json").read_text())
    assert report["status"] == "failed" and not report["registry_verified"]
    candidate = Agent.from_smcp_registry(str(output / "candidate-registry.json"))
    assert [t.name for t in candidate.tools] == [fixture_tool.name]
    assert os.environ.get("BLASTAI_MODEL") == original_model
