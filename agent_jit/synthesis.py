"""Run original browser-guided tool synthesis as explicit offline preparation."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlparse
from unittest.mock import patch

from agent_jit.browser_model import BrowserModel
from agent_jit.budget import Ledger
from agent_jit.process import await_with_deadline


async def synthesize_tools(
    task: str,
    url: str,
    output: Path,
    model: str,
    ledger_path: Path,
    budget_usd: float,
    registry: Path | None = None,
    timeout_s: int = 180,
) -> dict:
    """Create candidate SMCP tools with the original synthesis agent.

    Args:
        task: Actions and reusable tools to synthesize on the benchmark page.
        url: Initial HTTP(S) URL. Use a synthetic application, not production data.
        output: New directory for candidate registry and build metadata.
        model: Explicit bounded OpenAI model for all synthesis and helper calls.
        ledger_path: Shared campaign spending ledger.
        budget_usd: Cumulative ledger cap, including auxiliary inference.
        registry: Optional initial tools to extend; the source is never overwritten.
        timeout_s: Cooperative execution deadline in seconds.

    Returns:
        Build metadata. A generated registry remains a candidate until independently
        tested; agent self-reported completion does not verify its tool semantics.

    Raises:
        ValueError: If inputs are invalid or no SMCP tools are produced.
        TimeoutError: If the synthesis run exceeds its deadline.
    """
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    from browser_use import Browser
    from patchright.async_api import async_playwright
    from blastai.agents.executor import AgentExecutor
    from blastai.agents.llm_factory import LLMFactory
    from blastai.agents.models import Agent

    origin = urlparse(url)
    if (
        origin.scheme not in ("http", "https")
        or not origin.hostname
        or not task.strip()
    ):
        raise ValueError("Provide a task and an HTTP(S) benchmark URL")
    if not 1 <= timeout_s <= 3600 or output.exists():
        raise ValueError("Use a new output directory and timeout in 1..3600 seconds")
    base = Agent.from_smcp_registry(str(registry)) if registry else Agent()
    agent = base.derive_synthesis_agent()
    model_client = BrowserModel(model, Ledger(ledger_path, budget_usd, 128), 4096)
    output.mkdir(parents=True)
    report = {
        "schema_version": "1.0",
        "kind": "offline_tool_synthesis",
        "status": "failed",
        "model": model,
        "task": task,
        "initial_url": url,
        "ledger": str(ledger_path),
        "initial_tool_count": len(base.tools),
        "registry_verified": False,
    }
    if registry:
        report["initial_registry"] = {
            "path": str(registry.resolve()),
            "sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
        }
    started = time.perf_counter()
    executor = None

    def helper_model(**kwargs):
        # ask_html otherwise constructs an ambient, unaccounted provider client.
        if kwargs.get("model_name") != model or kwargs.get("provider") != "openai":
            raise ValueError("Unbudgeted auxiliary synthesis model requested")
        return model_client.chat

    try:
        with tempfile.TemporaryDirectory(prefix="agent-jit-synthesis-") as profile:
            async with async_playwright() as pw:
                browser = Browser(
                    headless=True,
                    executable_path=pw.chromium.executable_path,
                    user_data_dir=profile,
                )
                executor = AgentExecutor(
                    agent=agent,
                    llm=model_client.chat,
                    codegen_llm=model_client.chat,
                    browser=browser,
                    use_vision=True,
                    allowed_domains=[origin.hostname],
                    stop_if_codegen_fails=True,
                    disable_ai_exec_fallback=True,
                )
                try:
                    with (
                        patch.dict(
                            os.environ,
                            {
                                "BLASTAI_MODEL": model,
                                "BLASTAI_PROVIDER": "openai",
                                "ANONYMIZED_TELEMETRY": "false",
                            },
                        ),
                        patch.object(LLMFactory, "create_llm", helper_model),
                    ):
                        history = await await_with_deadline(
                            executor.run(task, mode="loop", initial_url=url), timeout_s
                        )
                    if history is None:
                        raise ValueError("Synthesis agent returned no result")
                    report["reported_success"] = bool(history.is_successful())
                    (output / "answer.txt").write_text(history.final_result() or "")
                    report["status"] = (
                        "generated" if history.is_successful() else "incomplete"
                    )
                finally:
                    await executor.cleanup()
                    executor = None
    finally:
        # Partial candidate tools are useful diagnostics even after timeout/error.
        try:
            agent.to_smcp_registry(str(output / "candidate-registry.json"))
            saved = Agent.from_smcp_registry(str(output / "candidate-registry.json"))
            report["tool_names"] = [t.name for t in saved.tools]
            report["offline_s"] = time.perf_counter() - started
            (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
        finally:
            await model_client.close()
    if not report["tool_names"]:
        raise ValueError("Synthesis produced no candidate SMCP tools")
    return report
