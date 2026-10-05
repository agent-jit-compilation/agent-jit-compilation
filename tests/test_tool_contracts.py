"""Checks on supplied registries and the recovered contract validator."""

import json
from pathlib import Path

from experiments.validate_tools import (
    check_circular_pretools,
    check_pretools_exist,
    check_state_flow,
)


def test_all_supplied_registries_have_consistent_declared_contracts():
    root = Path(__file__).resolve().parents[1]
    registries = sorted((root / "data/registries").glob("*.json"))
    assert len(registries) == 5
    for path in registries:
        tools = json.loads(path.read_text())["tools"]
        assert not check_circular_pretools(tools), path.name
        assert not check_pretools_exist(tools), path.name
        assert not check_state_flow(tools, {"page_type": "home"}), path.name


def test_disjunctive_preconditions_accept_one_producer_but_reject_no_producer():
    tools = [
        {"name": "open_orders", "post": {"page_type": "orders"}},
        {"name": "list_orders", "pre": {"page_type": "orders|cancelled"}},
    ]
    assert check_state_flow(tools, {}) == []
    tools[0]["post"]["page_type"] = "home"
    assert len(check_state_flow(tools, {})) == 1


def test_missing_and_cyclic_dependencies_are_rejected():
    tools = [{"name": "a", "pre_tools": {"item": ["missing"]}}]
    assert check_pretools_exist(tools)
    tools = [
        {"name": "a", "pre_tools": {"item": ["b"]}},
        {"name": "b", "pre_tools": {"item": ["a"]}},
    ]
    assert check_circular_pretools(tools)
