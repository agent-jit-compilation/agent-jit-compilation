"""Scheduler integration fixtures; no model or benchmark calls."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_jit.browser_model import bounded_payload
from blast_scheduler.online.scheduler import Scheduler, SchedulerPlan, ElementCount

ROOT = Path(__file__).resolve().parents[1]


def test_predictor_loads_supplied_cache_and_fails_on_unknown_elements():
    wrong = SchedulerPlan(
        explanation="fixture",
        elements=[
            ElementCount(
                element_name="not-in-cache",
                required=True,
                count=1,
                destination_page="main",
            )
        ],
    )
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=wrong)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    scheduler = Scheduler(
        client,
        "dashdish",
        schema_root=str(ROOT / "data/scheduler/saved_schemas"),
        element_latencies_file=str(
            ROOT / "data/scheduler/saved_sites/dashdish/element_latencies_gpt-4.1.json"
        ),
    )
    assert scheduler.valid_elements and scheduler.page_schemas
    with pytest.raises(ValueError, match="five attempts"):
        scheduler.get_sequential_usage("fixture")
    assert len(calls) == 5
    assert "RETRY:" in calls[-1]["input"][1]["content"]


def test_screenshot_budget_preserves_payload_and_refuses_unsupported_inputs():
    value = {
        "model": "gpt-4.1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Page"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,fixture",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
    }
    normalized = bounded_payload(value)
    assert len(normalized["messages"][0]["content"]) >= 4096
    assert isinstance(value["messages"][0]["content"], list)
    value["messages"][0]["content"][1]["image_url"]["url"] = (
        "https://example.test/image.png"
    )
    with pytest.raises(ValueError, match="inline"):
        bounded_payload(value)
    with pytest.raises(ValueError, match="one non-streamed"):
        bounded_payload({"n": 2})


@pytest.mark.skipif(
    importlib.util.find_spec("browser_use") is None
    or importlib.util.find_spec("markdownify") is None,
    reason="Browser extra required for original execution fixtures",
)
def test_hedge_skips_failed_finisher_and_stops_losing_workers(monkeypatch):
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.delenv("LMNR_PROJECT_API_KEY", raising=False)
    # Import engine first to preserve the original package's import ordering.
    from blastai.engine import Engine  # noqa: F401
    from blastai.tools import Tools

    async def exercise():
        stopped = []

        async def end_task(tid):
            stopped.append(tid)

        async def get_result(tid):
            await asyncio.sleep({"failed": 0, "winner": 0.01, "slow": 0.5}[tid])
            return SimpleNamespace(
                is_successful=lambda: tid != "failed", final_result=lambda: tid
            )

        tools = Tools.__new__(Tools)
        tools.resource_manager = SimpleNamespace(end_task=end_task)
        scheduler = SimpleNamespace(get_task_result=get_result, tasks={})
        result = await tools._get_first_subtask_result(
            scheduler, ["failed", "winner", "slow"], as_final=True
        )
        assert result.success and result.extracted_content == "winner"
        assert set(stopped) == {"failed", "slow"}

    asyncio.run(exercise())


@pytest.mark.skipif(
    importlib.util.find_spec("browser_use") is None
    or importlib.util.find_spec("markdownify") is None,
    reason="Browser extra required",
)
def test_parallel_state_merge_uses_changes_from_multiple_workers(monkeypatch):
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    from blastai.engine import Engine  # noqa: F401
    from experiments.task_state_utils import merge_parallel_final_states
    from agent_jit.adapters.scheduler import StateLogger

    states = {
        "A": {
            "actionhistory": [{"timestamp": 2}],
            "differences": {"emails": {"deleted": [{"id": "one"}]}},
        },
        "B": {
            "actionhistory": [{"timestamp": 1}],
            "differences": {"emails": {"deleted": [{"id": "two"}]}},
        },
    }
    result = merge_parallel_final_states(states, StateLogger())
    assert [x["timestamp"] for x in result["actionhistory"]] == [1, 2]
    assert {x["id"] for x in result["differences"]["emails"]["deleted"]} == {
        "one",
        "two",
    }
    assert result["_metadata"]["num_tasks_merged"] == 2


def test_auxiliary_screenshots_have_a_separate_price_bound():
    payload = {
        "model": "gpt-4.1-mini-2025-04-14",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,fixture",
                            "detail": "high",
                        },
                    }
                ],
            }
        ],
    }
    # Current documented patch budget is 6144 with a 1.62 multiplier.
    assert len(bounded_payload(payload)["messages"][0]["content"]) >= 6144 * 1.62
    payload["model"] = "unpriced-model"
    with pytest.raises(ValueError, match="price bound"):
        bounded_payload(payload)


def test_model_pool_routes_auxiliary_calls_and_closes_all_clients(
    monkeypatch, tmp_path
):
    from agent_jit import browser_model
    from agent_jit.budget import Ledger

    seen, closed = {}, []
    ledger = Ledger(tmp_path / "ledger.json", 1, 10)

    class Client:
        def __init__(self, model, shared, tokens):
            assert shared is ledger
            self.model = model
            self.chat = object()
            seen[model] = self.chat

        async def close(self):
            closed.append(self.model)
            if self.model == "gpt-4.1":
                raise RuntimeError("fixture close failure")

    monkeypatch.setattr(browser_model, "BrowserModel", Client)
    pool = browser_model.BrowserModelPool(["gpt-4.1", "gpt-4.1-mini"], ledger)
    main = pool.factory("openai:gpt-4.1")
    mini = pool.factory("gpt-4.1-mini")
    assert main is not mini and pool.factory("gpt-4.1-mini") is mini
    with pytest.raises(ValueError, match="Unbudgeted"):
        pool.factory("gpt-4.1-2025-04-14")
    with pytest.raises(RuntimeError, match="close"):
        asyncio.run(pool.close())
    assert set(closed) == {"gpt-4.1", "gpt-4.1-mini"}
