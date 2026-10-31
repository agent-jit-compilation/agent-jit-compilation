from pathlib import Path
import pytest
from agent_jit.planner import check, rank
from blastai.agents import codecheck

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "data/registries/dashdish.json"
STATE = {"page_type": "home"}


def test_real_registry_state_violation_and_cost_selection():
    paths = [ROOT / f"examples/plans/{x}.py" for x in ("invalid", "inference", "valid")]
    result = rank(paths, REG, STATE)
    invalid, inference, valid = result["candidates"]
    assert not invalid["valid"] and "page_type=store" in invalid["error"]
    assert inference["valid"] and valid["valid"]
    assert inference["estimated_cost"] - valid["estimated_cost"] == pytest.approx(10)
    assert result["selected"] == str(paths[2])


def test_missing_output_field_is_type_error(tmp_path):
    p = tmp_path / "plan.py"
    p.write_text(
        "stores = await list_all_stores(detailed=True)\nresult = stores.items[0].review_count\n"
    )
    result = check(p, REG, STATE)
    assert not result["valid"] and "review_count" in result["error"]


def test_missing_typechecker_is_not_success(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(codecheck.subprocess, "run", missing)
    assert codecheck.check_ty_types("x=1")[0] is False


def test_cfg_exception_is_not_success(monkeypatch):
    from blastai.agents.models import Agent

    def broken(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(codecheck.CFGBuilder, "build", broken)
    valid, error = codecheck.check_code_candidate(
        "x=1", Agent.from_smcp_registry(str(REG))
    )
    assert not valid and "infrastructure failure" in error


def test_truncated_and_no_tool_candidates_are_never_selected(tmp_path):
    import json
    from dataclasses import asdict
    from blastai.agents.codegen import CodeCandidate, LLMTiming
    from agent_jit.adapters.planner import recorded_candidates, reject_incomplete, select_candidate

    # Plans that stop early or never touch the app do the least, so they rank cheapest.
    cut = CodeCandidate("stores = await list_stores()", 2.0, True, llm_timings=[LLMTiming(8.3, total_tokens=1024)])
    text = CodeCandidate("result = 'To: a@example.com'", 0.0, True, llm_timings=[LLMTiming(2.0, total_tokens=40)])
    whole = CodeCandidate("await send_email(to='a@example.com')\nresult = 'sent'", 9.0, True,
                          llm_timings=[LLMTiming(5.1, total_tokens=600)])
    assert select_candidate([cut, text, whole], "best") is text
    (tmp_path / "candidates.json").write_text(json.dumps(
        [asdict(cut), {"is_valid": False, "error_type": "TimeoutError"}, asdict(text), asdict(whole)]))
    (tmp_path / "response.json").write_text(json.dumps({"timing": {"planning_s": 12.5}}))
    replayed, planning = recorded_candidates(tmp_path)
    assert planning == 12.5 and replayed[1] is None
    tools = {"list_stores", "send_email"}
    replayed = [reject_incomplete(c, 1024, tools) for c in replayed]
    assert select_candidate(replayed, "best").code == whole.code
    assert replayed[0].validation_error == "Output reached the 1024-token limit"
    assert replayed[2].validation_error == "Plan calls no application tool"
    assert reject_incomplete(whole, 2048, tools) is whole and whole.is_valid


def test_plans_are_told_the_user_cannot_answer():
    from blastai.agents.codegen import CodeGenerator
    from blastai.agents.models import Agent

    from types import SimpleNamespace

    generator = CodeGenerator(Agent.from_smcp_registry(str(REG)), llm=SimpleNamespace(), state_aware=True)
    rules = generator._generate_rules(STATE)
    assert "Do not ask for input or confirmation" in rules


def test_best_and_worst_selection_never_choose_invalid_candidates():
    from types import SimpleNamespace
    from agent_jit.adapters.planner import select_candidate

    low = SimpleNamespace(rank=3.1, is_valid=True)
    high = SimpleNamespace(rank=13.1, is_valid=True)
    invalid = SimpleNamespace(rank=999, is_valid=False)
    candidates = [None, low, invalid, high]
    assert select_candidate(candidates, "best") is low
    assert select_candidate(candidates, "worst") is high
    with pytest.raises(RuntimeError, match="No valid"):
        select_candidate([invalid, None], "worst")
    with pytest.raises(ValueError, match="selection"):
        select_candidate(candidates, "unknown")


def test_live_generation_and_static_rank_use_identical_registry_costs():
    import importlib.util

    if importlib.util.find_spec("browser_use") is None:
        pytest.skip("Browser extra required to import original generator")
    from blastai.agents.codegen import CodeGenerator
    from blastai.agents.models import Agent

    generator = CodeGenerator(Agent.from_smcp_registry(str(REG)), llm=object())
    path = ROOT / "examples/plans/valid.py"
    offline = check(path, REG, STATE)["estimated_cost"]
    assert offline > 0
    assert generator._compute_candidate_cost(path.read_text()) == pytest.approx(offline)


def test_structured_execution_answers_are_json_not_python_repr():
    import json
    from pydantic import BaseModel
    from agent_jit.adapters.planner import answer_text

    class Item(BaseModel):
        name: str
        available: bool

    value = {"items": [Item(name="O'Brien’s", available=True)], "missing": None}
    assert json.loads(answer_text(value)) == {
        "items": [{"name": "O'Brien’s", "available": True}],
        "missing": None,
    }
    assert json.loads(answer_text(["Work", "Personal"])) == ["Work", "Personal"]
    assert answer_text("Keep this prose unchanged.") == "Keep this prose unchanged."
    assert json.loads(answer_text({"Mike", "Ana"})) == ["Ana", "Mike"]


def test_plan_json_loads_accepts_values_ai_eval_already_parsed():
    import asyncio
    import json
    from blastai.agents.local_python_executor import LocalPythonExecutor

    executor = LocalPythonExecutor()
    executor.send_variables({"parsed": {"name": "Gevi 10"}, "text": '{"name": "Gevi 12"}'})
    code = (
        "import json\n"
        "from json import loads\n"
        "result = [json.loads(parsed)['name'], loads(text)['name'], json.dumps([1])]"
    )
    output = asyncio.run(executor(code))
    assert output.error is None and output.output == ["Gevi 10", "Gevi 12", "[1]"]
    with pytest.raises(TypeError):
        json.loads({"name": "unchanged outside plans"})
