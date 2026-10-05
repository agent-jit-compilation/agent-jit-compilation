"""Incomplete offline preparation must not look successful to shell pipelines."""

import json

import pytest

from agent_jit import cli, synthesis


@pytest.mark.parametrize("status,exit_code", [("generated", 0), ("incomplete", 1)])
def test_synthesis_exit_code_preserves_build_status(
    tmp_path, monkeypatch, capsys, status, exit_code
):
    async def synthesize(*args):
        return {"status": status, "registry_verified": False, "tool_names": ["partial"]}

    monkeypatch.setattr(synthesis, "synthesize_tools", synthesize)
    result = cli.main(
        [
            "synthesize-tools",
            "--url",
            "https://example.test/",
            "--task",
            "List items",
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--budget-usd",
            "1",
            "--output",
            str(tmp_path / "tools"),
        ]
    )
    assert result == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == status and not report["registry_verified"]


@pytest.mark.parametrize("command", ["build-schemas", "label-traces"])
def test_preparation_request_ceiling_is_configurable(
    tmp_path, monkeypatch, capsys, command
):
    from agent_jit import schemas, traces

    seen = []

    def prepare(*args, **kwargs):
        seen.append(kwargs["max_requests"])
        return {"status": "success"}

    if command == "build-schemas":
        monkeypatch.setattr(schemas, "build_schemas", prepare)
        inputs = ["--config", str(tmp_path / "pages.json")]
    else:
        monkeypatch.setattr(traces, "label_traces", prepare)
        inputs = [
            "--trace-steps",
            str(tmp_path / "steps"),
            "--schema-root",
            str(tmp_path / "schemas"),
            "--application",
            "fixture",
        ]
    result = cli.main(
        [
            command,
            *inputs,
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--budget-usd",
            "5",
            "--max-requests",
            "400",
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert result == 0 and seen == [400]
