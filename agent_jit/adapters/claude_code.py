"""Claude Code with the agent-browser skill, driving the trial's browser over CDP.

Claude Code runs headless in a per-trial folder that holds only a copy of the
agent-browser skill, so the user's settings, plugins, hooks, memory and MCP servers
do not load. Its reported cost is Claude Code's own estimate at API list prices.
"""

import asyncio
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from . import AgentOutput

SKILL = Path.home() / ".claude/skills/agent-browser"

PROMPT = """You are completing a task on a web application for a user who cannot answer questions.
A browser is already open on the application at {url}. Use the agent-browser skill, and pass `--cdp {port}` to every agent-browser command so that it drives that browser.
Work in the tab that is already open: do not open new tabs, other websites, or another browser, and do not close the browser.

Task: {task}

When you finish, reply with only your final answer to the user. If the task asks you to do something, reply with a short confirmation of what you did."""


def command(cfg, prompt):
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", cfg["model"],
        "--max-budget-usd", str(cfg["run_budget_usd"]),
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--allowedTools", "Bash(agent-browser:*)", "Skill",
        "--disallowedTools", "WebFetch", "WebSearch", "Agent",
    ]


def session_name(run_id):
    # Each trial gets its own agent-browser daemon. A full run ID makes the daemon's
    # socket path too long and it fails to start, so use a short prefix.
    return "jit-" + run_id.replace("-", "")[:8]


def stop_daemon(session):
    # SIGKILL, so the daemon cannot close the shared browser as it exits.
    pid_file = Path(tempfile.gettempdir()) / f"agent-browser-{session}.pid"
    try:
        os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass


def parse(stdout):
    """The final answer and run diagnostics from Claude Code's JSON result."""
    result = json.loads(stdout)
    metadata = {
        "claude_code_subtype": result.get("subtype"),
        "claude_code_is_error": result.get("is_error"),
        "claude_code_cost_usd": result.get("total_cost_usd"),
        "claude_code_duration_s": (result.get("duration_ms") or 0) / 1000,
        "claude_code_turns": result.get("num_turns"),
        "claude_code_usage": result.get("usage"),
    }
    return result.get("result") or "", metadata


async def run(context):
    cfg = context.configuration
    workdir = context.output_dir / "claude-code"
    shutil.copytree(SKILL, workdir / ".claude/skills/agent-browser")
    prompt = PROMPT.format(
        url=context.task["initial_url"],
        port=urlparse(context.cdp_url).port,
        task=context.task["task_details"],
    )
    session = session_name(context.output_dir.name)
    env = dict(os.environ, AGENT_BROWSER_SESSION=session)
    process = await asyncio.create_subprocess_exec(
        *command(cfg, prompt),
        cwd=workdir,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await process.communicate()
    except BaseException:
        # A timeout cancels the adapter; stop Claude Code and its tool processes.
        os.killpg(process.pid, signal.SIGKILL)
        stop_daemon(session)
        raise
    (context.output_dir / "claude-code.json").write_bytes(stdout)
    (context.output_dir / "claude-code.stderr").write_bytes(stderr)
    if process.returncode != 0 and not stdout.strip():
        raise RuntimeError(f"Claude Code exited {process.returncode}: {stderr.decode()[-500:]}")
    answer, metadata = parse(stdout)

    async def cleanup():
        # Runs after the final state is read from the browser.
        stop_daemon(session)

    return AgentOutput(answer, metadata=metadata, cleanup=cleanup)
