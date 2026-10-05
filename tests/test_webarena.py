"""WebArena config and ownership checks; these fixtures are not benchmark runs."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from agent_jit.catalog import tasks
from agent_jit.webarena import resolve_config, verify
from agent_jit.webarena_environment import Snapshot

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / ".downloads/webarena"


@pytest.mark.skipif(
    not CHECKOUT.exists()
    or importlib.util.find_spec("gymnasium") is None
    or importlib.util.find_spec("patchright") is None,
    reason="Prepared WebArena and browser extras required",
)
def test_upstream_auto_login_transfers_fresh_cookies_and_rejects_bad_session(tmp_path):
    """Exercise the unchanged upstream script against synthetic login HTTP pages."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import parse_qs
    from agent_jit.webarena_environment import login

    class LoginFixture(BaseHTTPRequestHandler):
        reject_gitlab = False

        def log_message(self, *args):
            pass

        def do_GET(self):
            cookies = self.headers.get("Cookie", "")
            if self.path == "/-/profile":
                if "gitlab_fixture=valid" not in cookies or self.reject_gitlab:
                    self.send_response(302)
                    self.send_header("Location", "/users/sign_in")
                    self.end_headers()
                    return
                body = "Authenticated GitLab fixture"
            elif self.path == "/user/MarvelsGrantMan136/account":
                body = (
                    "Delete account"
                    if "reddit_fixture=valid" in cookies
                    else "Unauthenticated"
                )
            elif self.path == "/users/sign_in":
                body = """<form method="post" action="/gitlab-auth">
                <input name="username" data-testid="username-field">
                <input name="password" type="password" data-testid="password-field">
                <button data-testid="sign-in-button">Sign in</button></form>"""
            else:
                body = """<form method="post" action="/reddit-auth">
                <label>Username<input name="username"></label>
                <label>Password<input name="password" type="password"></label>
                <button>Log in</button></form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            values = parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            site = "gitlab" if self.path == "/gitlab-auth" else "reddit"
            expected = (
                ("byteblaze", "hello1234")
                if site == "gitlab"
                else ("MarvelsGrantMan136", "test1234")
            )
            assert (values["username"][0], values["password"][0]) == expected
            self.send_response(302)
            self.send_header("Set-Cookie", f"{site}_fixture=valid; Path=/; HttpOnly")
            self.send_header(
                "Location",
                "/-/profile"
                if site == "gitlab"
                else "/user/MarvelsGrantMan136/account",
            )
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), LoginFixture)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"

    async def exercise():
        from patchright.async_api import async_playwright

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(tmp_path / "browser"), headless=True
            )
            try:
                page = context.pages[0]
                assert await context.cookies() == []
                evidence = await login(
                    page,
                    ["reddit", "gitlab"],
                    {"gitlab": endpoint, "reddit": endpoint},
                    root=ROOT,
                    checkout=CHECKOUT,
                    auth_folder=tmp_path / "auth",
                    timeout=30,
                )
                assert evidence["method"] == "upstream_auto_login"
                assert Path(evidence["state_file"]).name == "gitlab.reddit_state.json"
                assert {c["name"] for c in await context.cookies()} >= {
                    "gitlab_fixture",
                    "reddit_fixture",
                }
                await page.goto(endpoint + "/-/profile")
                assert "Authenticated GitLab" in await page.content()
                LoginFixture.reject_gitlab = True
                with pytest.raises(RuntimeError, match="GitLab benchmark login failed"):
                    await login(
                        page,
                        ["gitlab"],
                        {"gitlab": endpoint},
                        root=ROOT,
                        checkout=CHECKOUT,
                        auth_folder=tmp_path / "rejected",
                        timeout=30,
                    )
            finally:
                await context.close()

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_all_paper_web_configs_resolve_without_changing_assertions():
    rows = [t for t in tasks(ROOT) if t["benchmark"] == "WebArena"]
    assert len(rows) == 10
    for row in rows:
        before = json.dumps(row)
        config = resolve_config(
            row,
            {"gitlab": "http://localhost:18023", "reddit": "http://localhost:19999"},
        )
        assert "__GITLAB__" not in json.dumps(config)
        assert "__REDDIT__" not in json.dumps(config)
        assert config["task_id"] == int(row["id"].split("-")[1])
        assert (
            config["eval"]["reference_answers"]
            == row["verifier"]["configuration"]["eval"]["reference_answers"]
        )
        assert json.dumps(row) == before
    with pytest.raises(ValueError, match="Missing"):
        resolve_config(rows[0], {})


def test_snapshot_cleanup_only_removes_owned_created_ids(monkeypatch):
    snapshot = Snapshot({}, ["gitlab"], "fixture-owner")
    snapshot.containers.extend([{"id": "created-1"}, {"id": "created-2"}])
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":
            return "fixture-owner" if args[-1] == "created-1" else "different-owner"
        return ""

    monkeypatch.setattr(snapshot, "docker", docker)
    with pytest.raises(RuntimeError, match="ownership"):
        snapshot.close()
    assert [c for c in calls if c[0] == "rm"] == [("rm", "-f", "-v", "created-1")]


def test_start_failure_preserves_created_id_for_cleanup(monkeypatch):
    snapshot = Snapshot({}, ["reddit"], "fixture-owner")
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:immutable-fixture"}])
        if args[0] == "create":
            assert "--pull=never" in args and args[-1] == "sha256:immutable-fixture"
            assert not any(a in args for a in ("-v", "--mount", "--volume"))
            return "just-created"
        if args[0] == "start":
            raise RuntimeError("fixture port conflict")
        if args[0] == "inspect":
            return "fixture-owner"
        return ""

    monkeypatch.setattr(snapshot, "docker", docker)
    with pytest.raises(RuntimeError, match="port conflict"):
        snapshot.start()
    snapshot.close()
    assert calls[-1] == ("rm", "-f", "-v", "just-created")


@pytest.mark.skipif(
    not CHECKOUT.exists() or importlib.util.find_spec("gymnasium") is None,
    reason="prepare-webarena and the webarena extra are required",
)
def test_original_string_evaluator_and_live_dom_fixture(tmp_path):
    verify(ROOT, CHECKOUT)
    env = dict(os.environ, NLTK_DATA=str(ROOT / ".downloads/nltk"))
    # Exact native match is deliberately strict; no LLM may upgrade this result.
    row = next(t for t in tasks(ROOT) if t["id"] == "webarena-308")
    cfg = resolve_config(row, {"gitlab": "http://localhost:18023"})
    config_file = tmp_path / "task.json"
    config_file.write_text(json.dumps(cfg))

    def evaluate(text, cdp=None):
        req = tmp_path / "request.json"
        out = tmp_path / "result.json"
        req.write_text(
            json.dumps(
                {
                    "checkout": str(CHECKOUT),
                    "endpoints": {"gitlab": "http://localhost:18023"},
                    "config_file": str(config_file),
                    "text": text,
                    "cdp_url": cdp,
                }
            )
        )
        subprocess.run(
            [sys.executable, "-m", "agent_jit.webarena", str(req), str(out)],
            env=env,
            check=True,
            capture_output=True,
            timeout=30,
        )
        return json.loads(out.read_text())

    assert evaluate("Shawn Allen")["correct"]
    assert not evaluate("The most active contributor is Shawn Allen.")["correct"]
    if importlib.util.find_spec("patchright") is None:
        pytest.skip("Browser extra required for DOM fixture")

    async def exercise():
        from patchright.async_api import async_playwright

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"),
                headless=True,
                args=[f"--remote-debugging-port={port}"],
            )
            try:
                page = browser.pages[0]
                await page.set_content(
                    '<div class="cover-status"><span>Enjoying life</span></div>'
                )
                original = next(t for t in tasks(ROOT) if t["id"] == "webarena-419")
                fixture = resolve_config(original, {"gitlab": "http://localhost:18023"})
                # Use the real saved locator/assertion, changing only the fixture URL.
                fixture["eval"]["program_html"][0]["url"] = "last"
                config_file.write_text(json.dumps(fixture))
                assert (
                    await asyncio.to_thread(evaluate, "", f"http://127.0.0.1:{port}")
                )["correct"]
                await page.set_content(
                    '<div class="cover-status"><span>Wrong state</span></div>'
                )
                assert not (
                    await asyncio.to_thread(evaluate, "", f"http://127.0.0.1:{port}")
                )["correct"]
                # Evaluation must leave the owner's connection usable.
                assert await page.title() == ""
            finally:
                await browser.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "native_score,judge_result,expected,calls",
    [
        (1, 0, 1, 0),
        (0, 1, 1, 1),
        (0, 0, 0, 1),
        (0, "error", 0, 1),
    ],
)
def test_explicit_historical_judge_preserves_native_passes_and_failures(
    tmp_path, monkeypatch, native_score, judge_result, expected, calls
):
    from agent_jit import webarena, webarena_judge

    seen = []
    monkeypatch.setattr(
        webarena, "load_evaluator", lambda *a: lambda *a: lambda *a: native_score
    )

    async def judge(*args):
        seen.append(args)
        if judge_result == "error":
            raise RuntimeError("fixture failure")
        return {"score": judge_result}

    monkeypatch.setattr(webarena_judge, "judge_answer", judge)
    config = tmp_path / "task.json"
    config.write_text(
        json.dumps(
            {
                "intent": "name",
                "eval": {
                    "eval_types": ["string_match"],
                    "reference_answers": {"exact_match": "Shawn Allen"},
                },
            }
        )
    )
    result = webarena.evaluate_native(
        "unused",
        {},
        config,
        "Shawn",
        llm_judge=True,
        ledger_path=tmp_path / "ledger.json",
    )
    assert result["score"] == expected and result["native_score"] == native_score
    assert len(seen) == calls
    if judge_result == "error":
        assert result["judge"]["used_native_score"]
