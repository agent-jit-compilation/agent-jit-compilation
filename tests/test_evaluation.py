import asyncio
import json
from pathlib import Path
import pytest
from agent_jit.catalog import task
from agent_jit.evaluation import evaluate

ROOT = Path(__file__).resolve().parents[1]


def test_shared_evaluator_rejects_wrong_order_and_awards_partial_credit():
    t = task(ROOT, "dashdish-custom-5")
    good = asyncio.run(
        evaluate(
            t,
            json.dumps(
                {"restaurants": ["Gambinos New York Subs", "Wingstop", "Man vs. Fries"]}
            ),
            None,
            "json",
        )
    )
    bad = asyncio.run(
        evaluate(
            t,
            json.dumps(
                {"restaurants": ["Wingstop", "Gambinos New York Subs", "Man vs. Fries"]}
            ),
            None,
            "json",
        )
    )
    assert good["correct"] and good["score"] == 1
    assert not bad["correct"] and bad["score"] == pytest.approx(1 / 3)


def test_action_evaluation_cannot_fall_back_to_answer_text():
    t = task(ROOT, "dashdish-custom-3")
    with pytest.raises(ValueError, match="requires /finish"):
        asyncio.run(evaluate(t, "I placed the order", None, "json"))


def test_historical_parser_uses_original_prompt_schema_and_partial_credit(
    tmp_path, monkeypatch
):
    pytest.importorskip("browser_use")
    from types import SimpleNamespace
    from agent_jit import native_model

    calls = []
    closed = []

    class Parser:
        def __init__(self, model, ledger, max_output_tokens):
            assert model == "claude-sonnet-4-20250514" and max_output_tokens == 8192

        async def ainvoke(self, messages, output_format):
            calls.append(messages[0].content)
            return SimpleNamespace(
                completion=output_format.model_validate(
                    {
                        "restaurants": [
                            "Wingstop",
                            "Gambinos New York Subs",
                            "Man vs. Fries",
                        ]
                    }
                )
            )

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(native_model, "NativeModel", Parser)
    result = asyncio.run(
        evaluate(
            task(ROOT, "dashdish-custom-5"),
            "answer",
            None,
            "historical",
            tmp_path / "ledger.json",
        )
    )
    assert not result["correct"] and result["score"] == pytest.approx(1 / 3)
    assert calls == [
        "Convert the following text to the requested structured JSON format.\n\nText:\nanswer"
    ]
    assert closed


@pytest.mark.parametrize(
    "answer",
    [
        {"correct": "false", "score": 1.0},
        {"correct": True, "score": 0.5},
        {"correct": False, "score": "0"},
        {"correct": False, "score": float("nan")},
        {"correct": False, "score": True},
        {"score": 1.0},
    ],
)
def test_custom_verifier_rejects_invalid_result_contract(monkeypatch, answer):
    import sys
    from types import ModuleType

    module = ModuleType("strict_verifier_fixture")
    module.verify = lambda *args: answer
    monkeypatch.setitem(sys.modules, module.__name__, module)
    t = {"verifier": {"type": "custom", "callable": "strict_verifier_fixture:verify"}}
    with pytest.raises(ValueError, match="Custom verifier"):
        asyncio.run(evaluate(t, "answer", None, "json"))


def test_every_shipped_real_evaluator_imports_with_installed_dependencies():
    import importlib
    from agent_jit.catalog import tasks

    for entry in tasks(ROOT):
        if entry["benchmark"] != "REAL":
            continue
        verifier = task(ROOT, entry["id"])["verifier"]
        module = importlib.import_module(
            verifier["script"].removesuffix(".py").replace("/", ".")
        )
        if verifier["type"] == "state_check":
            assert callable(module.evaluate)
        else:
            assert callable(module.validator.check_correctness)
