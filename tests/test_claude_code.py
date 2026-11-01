"""Command and result handling for the Claude Code adapter."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_jit.adapters.claude_code import command, parse, session_name, stop_daemon


def test_command_is_headless_isolated_and_capped():
    args = command({"model": "claude-sonnet-5-5", "run_budget_usd": 2}, "do it")
    assert args[:3] == ["claude", "-p", "do it"]
    for flag, value in (("--output-format", "json"), ("--model", "claude-sonnet-5-5"),
                        ("--max-budget-usd", "2"), ("--setting-sources", "project")):
        assert args[args.index(flag) + 1] == value
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert allowed == ["Bash(agent-browser:*)", "Skill"]


def test_parse_reads_answer_and_reported_cost():
    stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Done.",
                         "total_cost_usd": 0.42, "duration_ms": 61000, "num_turns": 18, "usage": {"input_tokens": 9}})
    answer, metadata = parse(stdout)
    assert answer == "Done."
    assert metadata["claude_code_cost_usd"] == 0.42 and metadata["claude_code_duration_s"] == 61
    assert parse(json.dumps({"subtype": "error_max_budget_usd", "is_error": True}))[0] == ""


def test_agent_browser_session_names_stay_short():
    assert session_name("2f6c1d7e-0000-4b8e-9a51-1d2e3f4a5b6c") == "jit-2f6c1d7e"


def test_stop_daemon_kills_the_sessions_process_only(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    daemon = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(tmp_path, "agent-browser-jit-1234abcd.pid").write_text(str(daemon.pid))
    stop_daemon("jit-other")
    assert daemon.poll() is None
    stop_daemon("jit-1234abcd")
    assert daemon.wait(timeout=5) == -9
