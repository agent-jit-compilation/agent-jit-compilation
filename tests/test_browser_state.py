"""State evaluation must retain the acting tab's history and selected endpoint."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from agent_jit import worker
from agent_jit.adapters import AgentOutput
from agent_jit.browser_state import state_provider


def test_state_provider_uses_current_agent_tab():
    visited = []

    async def goto(url):
        visited.append(url)

    async def evaluate(expression):
        return '{"count": 3}'

    async def current():
        return SimpleNamespace(goto=goto, evaluate=evaluate)

    task = {
        "benchmark": "custom",
        "initial_url": "http://fixture.test/app/",
        "verifier": {"state_url": "/snapshot"},
    }
    result = asyncio.run(
        state_provider(SimpleNamespace(get_current_page=current), task)()
    )
    assert result == {"count": 3}
    assert visited == ["http://fixture.test/snapshot"]


def test_worker_preserves_tab_local_state(tmp_path, monkeypatch):
    pytest.importorskip("patchright")
    task = {
        "id": "fixture",
        "benchmark": "custom",
        "application": "fixture",
        "initial_url": "http://fixture.test/",
        "verifier": {"type": "custom", "state_url": "/snapshot"},
    }
    monkeypatch.setattr(worker, "get_task", lambda *a: dict(task))
    adapter = ModuleType("fixture_state_adapter")

    async def run(context):
        async def snapshot(route):
            await route.fulfill(
                content_type="text/html",
                body="<pre></pre><script>document.querySelector('pre').textContent = JSON.stringify({label:sessionStorage.getItem('label')});</script>",
            )

        await context.page.route("**/snapshot", snapshot)
        await context.page.evaluate("sessionStorage.setItem('label', 'Work')")
        return AgentOutput("Created Work")

    adapter.run = run
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)

    async def evaluate(task, answer, state, *args, **kwargs):
        assert state == {"label": "Work"}
        return {"correct": True, "score": 1.0}

    monkeypatch.setattr(worker, "evaluate", evaluate)
    # Serve the initial page without depending on external DNS or network.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>State fixture</html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    task["initial_url"] = f"http://127.0.0.1:{server.server_port}/"
    try:
        result = asyncio.run(
            worker.run(
                {
                    "root": str(tmp_path),
                    "output_dir": str(tmp_path),
                    "task": "fixture",
                    "config": {
                        "adapter": adapter.__name__ + ":run",
                        "evaluator": "json",
                        "timeout_s": 5,
                    },
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result["status"] == "success", result
    assert json.loads((tmp_path / "final-state.json").read_text()) == {"label": "Work"}


def test_state_reader_waits_for_navigation_before_reading_json():
    pytest.importorskip("patchright")
    from agent_jit.browser_state import read_final_state

    async def exercise():
        from patchright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()

                async def route(request):
                    snapshot = request.request.url.endswith("/snapshot")
                    if snapshot:
                        await asyncio.sleep(0.2)
                    await request.fulfill(
                        content_type="text/html",
                        body=(
                            '<pre>{"label":"Work"}</pre>'
                            if snapshot
                            else '<pre>{"label":"stale"}</pre>'
                        ),
                    )

                await page.route("http://fixture.test/**", route)
                await page.goto("http://fixture.test/")
                pending = []

                async def goto(url):
                    # Like the CDP actor, return before the new document is ready.
                    pending.append(asyncio.create_task(page.goto(url)))

                actor = SimpleNamespace(goto=goto, evaluate=page.evaluate)
                task = {
                    "benchmark": "custom",
                    "initial_url": "http://fixture.test/",
                    "verifier": {"state_url": "/snapshot"},
                }
                result = await read_final_state(actor, task)
                await asyncio.gather(*pending)
                assert result == {"label": "Work"}
            finally:
                await browser.close()

    asyncio.run(exercise())
