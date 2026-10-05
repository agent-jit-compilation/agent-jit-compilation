import json
import pytest
from agent_jit.budget import Ledger, BudgetExceeded


def test_reservation_precedes_call_and_survives_reopen(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path, 0.02, 2)
    ledger.reserve("gpt-4.1", {"messages": [{"content": "test"}]}, 1024)
    with pytest.raises(BudgetExceeded):
        Ledger(path, 0.02, 2).reserve(
            "gpt-4.1", {"messages": [{"content": "test"}]}, 1024
        )
    d = json.loads(path.read_text())
    assert len(d["requests"]) == 1 and d["accounted_usd"] > 0


def test_actual_tokens_reconcile_but_do_not_reset_history(tmp_path):
    p = tmp_path / "ledger.json"
    ledger = Ledger(p, 1, 1)
    rid = ledger.reserve("gpt-4.1", {"messages": [{"content": "test"}]}, 1024)
    ledger.finish(rid, {"prompt_tokens": 100, "completion_tokens": 20})
    assert json.loads(p.read_text())["accounted_usd"] == pytest.approx(0.00036)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("gpt-4.1", {"messages": [{"content": "test"}]}, 1)


def test_no_unknown_model_or_image_costs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 1, 2)
    with pytest.raises(ValueError):
        ledger.reserve("unknown", {}, 1)
    with pytest.raises(ValueError):
        ledger.reserve(
            "gpt-4.1", {"messages": [{"content": [{"type": "image_url"}]}]}, 1
        )


def test_primary_and_auxiliary_models_share_one_spending_ceiling(tmp_path):
    path = tmp_path / "ledger.json"
    primary = Ledger(path, 0.01, 10)
    auxiliary = Ledger(path, 0.01, 10)
    first = primary.reserve("gpt-4.1", {}, 1)
    with pytest.raises(BudgetExceeded):
        auxiliary.reserve("gpt-4.1-mini-2025-04-14", {}, 1024)
    primary.finish(first, {"prompt_tokens": 100, "completion_tokens": 20})
    second = auxiliary.reserve("gpt-4.1-mini-2025-04-14", {}, 1024)
    auxiliary.finish(second, {"prompt_tokens": 100, "completion_tokens": 20})
    data = json.loads(path.read_text())
    assert data["accounted_usd"] == pytest.approx(0.00036 + 0.000072)
    assert data["requests"][1]["price_source"].endswith("gpt-4.1-mini")


def test_explicitly_raised_limit_preserves_charges_and_old_clients_keep_their_cap(
    tmp_path,
):
    path = tmp_path / "ledger.json"
    small = Ledger(path, 0.02, 100)
    rid = small.reserve("gpt-4.1", {}, 1024)
    with pytest.raises(BudgetExceeded):
        small.reserve("gpt-4.1", {}, 1024)
    large = Ledger(path, 20, 5000)
    large.reserve("gpt-4.1", {}, 1024)
    before = json.loads(path.read_text())
    assert len(before["requests"]) == 2 and before["accounted_usd"] > 0.02
    with pytest.raises(BudgetExceeded):
        small.reserve("gpt-4.1", {}, 1024)
    small.finish(rid, {"prompt_tokens": 100, "completion_tokens": 20})
    after = json.loads(path.read_text())
    assert len(after["requests"]) == 2
    assert after["budget_usd"] == 20  # finishing an older request cannot lower it
    assert after["requests"][0]["estimated_cost_usd"] == pytest.approx(0.00036)


def _reserve_concurrently(path):
    ledger = Ledger(path, 1, 7)
    try:
        rid = ledger.reserve("gpt-4.1", {}, 1)
    except BudgetExceeded:
        return False
    ledger.finish(rid, {"prompt_tokens": 100, "completion_tokens": 20})
    return True


def test_parallel_processes_share_request_limit_without_lost_charges(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing

    path = tmp_path / "ledger.json"
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        outcomes = list(pool.map(_reserve_concurrently, [path] * 24))
    data = json.loads(path.read_text())
    assert sum(outcomes) == 7
    assert len(data["requests"]) == 7
    assert data["accounted_usd"] == pytest.approx(7 * 0.00036)
