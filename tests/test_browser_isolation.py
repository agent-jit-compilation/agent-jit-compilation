"""Local fixture test of the browser isolation mechanism; not a benchmark result."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import tempfile
import threading
import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("patchright") is None,
    reason="Install the browser extra and Chromium for the local browser fixture",
)
def test_fresh_profiles_do_not_share_cookies_or_local_storage():
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Isolation fixture</body></html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def exercise():
        from patchright.async_api import async_playwright

        async with async_playwright() as pw:
            with (
                tempfile.TemporaryDirectory() as first,
                tempfile.TemporaryDirectory() as second,
            ):
                one = await pw.chromium.launch_persistent_context(first, headless=True)
                await one.pages[0].goto(f"http://127.0.0.1:{server.server_port}")
                await one.pages[0].evaluate(
                    "() => {localStorage.setItem('trial-marker','first');document.cookie='trial=first';}"
                )
                assert (
                    await one.pages[0].evaluate(
                        "() => localStorage.getItem('trial-marker')"
                    )
                    == "first"
                )
                await one.close()
                two = await pw.chromium.launch_persistent_context(second, headless=True)
                await two.pages[0].goto(f"http://127.0.0.1:{server.server_port}")
                assert (
                    await two.pages[0].evaluate(
                        "() => localStorage.getItem('trial-marker')"
                    )
                    is None
                )
                assert await two.pages[0].evaluate("() => document.cookie") == ""
                await two.close()

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
