"""Local Chromium fixture for multi-browser adapter evaluation/cleanup callbacks."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import sys
import threading
from types import ModuleType

import pytest
from agent_jit.adapters import AgentOutput
from agent_jit import worker


@pytest.mark.skipif(
    importlib.util.find_spec("patchright") is None, reason="Browser extra required"
)
@pytest.mark.parametrize("mode", ["evaluation_error", "cancel_raise", "cancel_return"])
def test_worker_lifecycle_and_deadline_accounting(tmp_path, monkeypatch, mode):
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>adapter lifecycle fixture</body></html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    task = {
        "id": "fixture",
        "benchmark": "REAL",
        "application": "fixture",
        "initial_url": f"http://127.0.0.1:{server.server_port}",
        "verifier": {"type": "state_check"},
    }
    monkeypatch.setattr(worker, "get_task", lambda *a: dict(task))
    adapter = ModuleType("fixture_lifecycle_adapter")

    async def run(context):
        events.append("agent")
        if mode.startswith("cancel_"):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events.append("cancelled")
                if mode == "cancel_raise":
                    raise RuntimeError("Task A failed to complete")
                return AgentOutput("apparent success")

        async def state():
            events.append("state")
            return {"observed": await context.page.locator("body").inner_text()}

        async def cleanup():
            events.append("cleanup")
            # The trial browser is still alive during adapter cleanup.
            assert "fixture" in await context.page.locator("body").inner_text()

        return AgentOutput("fixture output", state_provider=state, cleanup=cleanup)

    adapter.run = run
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)

    async def evaluate(
        task, text, final_state, *args, max_requests=128, parser_model=None
    ):
        assert max_requests == 128
        events.append("evaluation")
        assert final_state == {"observed": "adapter lifecycle fixture"}
        raise ValueError("fixture evaluator failure")

    monkeypatch.setattr(worker, "evaluate", evaluate)
    request = {
        "root": str(tmp_path),
        "task": "fixture",
        "output_dir": str(tmp_path),
        "config": {
            "adapter": adapter.__name__ + ":run",
            "evaluator": "json",
            "timeout_s": 5 if mode == "evaluation_error" else 0.01,
        },
    }
    try:
        result = asyncio.run(worker.run(request))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if mode.startswith("cancel_"):
        assert events == ["agent", "cancelled"]
        assert result["status"] == "timeout"
        assert not result["success"] and result["score"] == 0
        assert result["error"] == "Timeout during agent"
        return
    assert events == ["agent", "state", "evaluation", "cleanup"]
    assert (
        result["status"] == "evaluation_error"
        and not result["success"]
        and result["score"] == 0
    )
    assert result["timing"]["evaluation_s"] > 0
