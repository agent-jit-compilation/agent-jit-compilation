"""Original planner-study loop baselines, with optional application-level tools."""

from pathlib import Path
import time

from . import AgentOutput
from .registry import load_agent
from ..browser_model import BrowserModel
from ..budget import Ledger
from ..history import save_history
from ..browser_state import state_provider


async def run(context):
    from browser_use import Browser
    from blastai.agents.executor import AgentExecutor

    cfg = context.configuration
    use_tools = cfg.get("use_cached_tools", False)
    if not isinstance(use_tools, bool):
        raise ValueError("use_cached_tools must be a boolean")
    llm = BrowserModel(
        cfg["model"],
        Ledger(Path(cfg["ledger"]), cfg["budget_usd"], cfg.get("max_requests", 128)),
        cfg.get("max_output_tokens", 2048),
        temperature=0.5,
    )
    executor = None

    async def cleanup():
        try:
            if executor is not None:
                try:
                    if cfg.get("save_browser_history", False):
                        browser_agent = executor.browser_use_agent
                        if browser_agent is not None:
                            save_history(
                                browser_agent.history, context.output_dir, "agent"
                            )
                finally:
                    await executor.cleanup()
        finally:
            await llm.close()

    try:
        agent = load_agent(context, use_tools)
        executor = AgentExecutor(
            agent=agent,
            llm=llm.chat,
            codegen_llm=llm.chat,
            browser=Browser(cdp_url=context.cdp_url, keep_alive=True),
            timezone=cfg.get("timezone", "America/Los_Angeles"),
            use_vision=cfg.get("allow_vision", False),
            stop_if_codegen_fails=True,
            disable_ai_exec_fallback=True,
        )
        start = time.perf_counter()
        result = await executor.run(
            context.task["task_details"],
            mode="loop",
            initial_url=context.task["initial_url"],
        )
        elapsed = time.perf_counter() - start
        if result is None:
            raise RuntimeError("Browser-Use returned no history")
        return AgentOutput(
            text=result.final_result() or "",
            planning_s=0,
            execution_s=elapsed,
            metadata=dict(
                use_cached_tools=use_tools,
                cached_tool_count=len(agent.tools),
                reported_success=bool(result.is_successful()),
                temperature=0.5,
                allow_vision=cfg.get("allow_vision", False),
                max_steps=50,
            ),
            state_provider=state_provider(executor.browser, context.task),
            cleanup=cleanup,
        )
    except BaseException:
        await cleanup()
        raise
