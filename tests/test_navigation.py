"""Navigation regressions: resolve project names and never replay side effects."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_gitlab_name_resolution_and_destination_validation():
    pytest.importorskip("patchright")

    async def run():
        from patchright.async_api import async_playwright

        tool = next(
            t
            for t in json.loads((ROOT / "data/registries/gitlab.json").read_text())[
                "tools"
            ]
            if t["name"] == "goto_project"
        )
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()

                async def route(r):
                    url = r.request.url
                    if "/explore/projects?" in url:
                        body = (
                            '<a class="project" href="/wireservice/csvkit">CSVKit</a>'
                        )
                    elif url.endswith("/missing/repo"):
                        body = '<div class="error-page">404</div>'
                    else:
                        body = '<div class="project-home-panel">Project</div>'
                    await r.fulfill(body=body, content_type="text/html")

                await page.route("http://fixture.test/**", route)
                call = "async ([body, inputs, output]) => await new (Object.getPrototypeOf(async function(){}).constructor)('inputs','output',body)(inputs,output)"
                for name, path, links in [
                    (
                        "a11yproject",
                        "a11yproject/a11yproject.com",
                        '<a class="project" href="/a11yproject/a11yproject.com">A11y</a>',
                    ),
                    ("csvkit", "wireservice/csvkit", ""),
                    ("primer/design", "primer/design", ""),
                ]:
                    await page.goto("http://fixture.test/")
                    await page.set_content(links)
                    output = await page.evaluate(
                        call, [tool["execute"], {"project_path": name}, None]
                    )
                    assert output["matched_path"] == path
                    await page.wait_for_url("http://fixture.test/" + path)
                    assert (
                        await page.evaluate(
                            call, [tool["is_completed"], {"project_path": name}, output]
                        )
                        is True
                    )
                await page.goto("http://fixture.test/")
                await page.set_content(
                    '<a class="project" href="/one/csvkit">1</a><a class="project" href="/two/csvkit">2</a>'
                )
                with pytest.raises(Exception, match="Ambiguous project"):
                    await page.evaluate(
                        call, [tool["execute"], {"project_path": "csvkit"}, None]
                    )
                await page.goto("http://fixture.test/missing/repo")
                assert (
                    await page.evaluate(
                        call,
                        [
                            tool["is_completed"],
                            {"project_path": "missing/repo"},
                            {"matched_path": "missing/repo"},
                        ],
                    )
                )[0] is False
            finally:
                await browser.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        "Execution context was destroyed",
        "Cannot find context",
        "Inspected target navigated or closed",
    ],
)
def test_completion_recovers_navigation_without_repeating_action(error):
    pytest.importorskip("browser_use")
    from blastai.agents.tools_smcp import execute_smcp_tool

    async def run():
        executions = checks = 0

        async def evaluate(params, session_id):
            nonlocal executions, checks
            code = params["expression"]
            if "ready: document.readyState" in code:
                return {
                    "result": {
                        "value": {
                            "url": "http://fixture.test/done",
                            "ready": "complete",
                        }
                    }
                }
            if "const output =" in code:
                checks += 1
                if checks == 1:
                    raise RuntimeError(error)
                return {"result": {"value": {"success": True, "attempts": 1}}}
            executions += 1
            return {"result": {"value": {"success": True}}}

        session = SimpleNamespace(
            session_id="fixture",
            cdp_client=SimpleNamespace(
                send=SimpleNamespace(Runtime=SimpleNamespace(evaluate=evaluate))
            ),
        )

        async def get_session():
            return session

        executor = SimpleNamespace(
            browser=SimpleNamespace(get_or_create_cdp_session=get_session),
            current_mode="code",
        )
        tool = SimpleNamespace(
            name="submit",
            is_ready="",
            execute="return {success:true};",
            is_completed="return true;",
        )
        result = await execute_smcp_tool(executor, tool, {})
        assert result["success"] and executions == 1 and checks == 2

    asyncio.run(run())


def test_closed_or_wrong_page_does_not_count_as_completed():
    pytest.importorskip("browser_use")
    from blastai.agents.tools_smcp import wait_for_document, execute_smcp_tool

    async def run():
        calls = 0

        async def evaluate(params, session_id):
            nonlocal calls
            calls += 1
            if "ready: document.readyState" in params["expression"]:
                if calls < 3:
                    return {"result": {"value": {"url": "", "ready": "loading"}}}
                return {
                    "result": {
                        "value": {
                            "url": "http://fixture.test/wrong",
                            "ready": "complete",
                        }
                    }
                }
            if "const output =" in params["expression"]:
                return {
                    "result": {
                        "value": {"success": False, "reason": "wrong destination"}
                    }
                }
            return {"result": {"value": {"success": True}}}

        session = SimpleNamespace(
            session_id="fixture",
            cdp_client=SimpleNamespace(
                send=SimpleNamespace(Runtime=SimpleNamespace(evaluate=evaluate))
            ),
        )

        async def get_session():
            return session

        browser = SimpleNamespace(get_or_create_cdp_session=get_session)
        assert await wait_for_document(browser) == "http://fixture.test/wrong"
        tool = SimpleNamespace(
            name="submit",
            is_ready="",
            execute="return {success:true};",
            is_completed="return false;",
        )
        with pytest.raises(RuntimeError, match="wrong destination"):
            await execute_smcp_tool(
                SimpleNamespace(browser=browser, current_mode="code"), tool, {}
            )

        async def closed(*a, **kw):
            raise RuntimeError("Target closed")

        session.cdp_client.send.Runtime.evaluate = closed
        with pytest.raises(RuntimeError, match="Target closed"):
            await wait_for_document(browser)

    asyncio.run(run())


def test_commit_dates_do_not_depend_on_browser_timezone():
    pytest.importorskip("patchright")

    async def run():
        from patchright.async_api import async_playwright

        tool = next(
            t
            for t in json.loads((ROOT / "data/registries/gitlab.json").read_text())[
                "tools"
            ]
            if t["name"] == "get_commits"
        )
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for timezone in ["UTC", "America/Chicago"]:
                    context = await browser.new_context(timezone_id=timezone)
                    page = await context.new_page()
                    await page.set_content(
                        '<div class="commit-content"><a class="commit-author-link">Eric</a><time datetime="2023-03-02T03:43:12Z"></time></div>'
                    )
                    result = await page.evaluate(
                        "body => new Function('inputs',body)({date:'3/2'})",
                        tool["execute"],
                    )
                    assert len(result["commits"]) == 1
                    await context.close()
            finally:
                await browser.close()

    asyncio.run(run())


def test_port_probe_reuses_time_wait_but_rejects_listener(monkeypatch):
    import socket
    from agent_jit.webarena_environment import preflight, Snapshot

    def docker(self, *args, **kwargs):
        if args[0] == "info":
            return '{"OSType":"linux"}'
        if args[0] == "context":
            return '[{"Endpoints":{"docker":{"Host":"unix:///fixture.sock"}}}]'
        return "[{}]"

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(Snapshot, "docker", docker)
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    config = {"sites": {"gitlab": {"port": port}}}
    client = socket.create_connection(("127.0.0.1", port))
    connection, _ = server.accept()
    try:
        with pytest.raises(ValueError, match="occupied"):
            preflight(config, ["gitlab"])
        # Active close on the server leaves TIME_WAIT after both ends close.
        connection.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        client.close()
        connection.close()
        server.close()
        preflight(config, ["gitlab"])
    finally:
        client.close()
        connection.close()
        server.close()


def test_gitlab_navigation_contract_passes_actual_planner_validation(tmp_path):
    from agent_jit.planner import check

    code = tmp_path / "plan.py"
    code.write_text(
        'await goto_project(project_path="a11yproject")\nawait goto_project_commits()\nresult = await get_commits(date="3/2")\n'
    )
    result = check(
        code, ROOT / "data/registries/gitlab.json", {"page_type": "dashboard"}
    )
    assert result["valid"], result


def test_gitlab_navigation_contract_passes_live_generator_validation():
    pytest.importorskip("browser_use")
    from blastai.agents.codegen import CodeGenerator
    from blastai.agents.models import Agent

    generator = CodeGenerator(
        Agent.from_smcp_registry(str(ROOT / "data/registries/gitlab.json")),
        llm=object(),
    )
    valid, error = generator._check_candidate(
        'await goto_project("primer/design")\nawait goto_project_contributors()\nresult = await list_project_contributors()\n',
        initial_state={"page_type": "dashboard"},
    )
    assert valid, error


@pytest.mark.parametrize(
    "button",
    [
        '<button aria-label="Proceed to checkout">Continue</button>',
        "<button>Proceed to checkout</button>",
    ],
)
def test_omnizon_checkout_supports_accessible_label_and_visible_text(button):
    pytest.importorskip("patchright")

    async def run():
        from patchright.async_api import async_playwright

        tool = next(
            t
            for t in json.loads((ROOT / "data/registries/omnizon.json").read_text())[
                "tools"
            ]
            if t["name"] == "proceed_to_checkout"
        )
        call = "async body => await new (Object.getPrototypeOf(async function(){}).constructor)(body)()"
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content("<button>Add to cart</button>" + button)
                await page.evaluate("""() => {
                    window.clicks = 0;
                    document.querySelectorAll('button')[1].onclick = () => {
                        window.clicks += 1;
                        document.body.innerHTML = '<button aria-label="Place your order">Place your order</button>';
                    };
                }""")
                assert await page.evaluate(call, tool["is_ready"]) is True
                await page.evaluate(call, tool["execute"])
                assert await page.evaluate(call, tool["is_completed"]) is True
                assert await page.evaluate("window.clicks") == 1
                for markup in [
                    "<button disabled>Proceed to checkout</button>",
                    "<button>Add to cart</button>",
                    "<button>Proceed to checkout</button>" * 2,
                ]:
                    await page.set_content(markup)
                    assert (await page.evaluate(call, tool["is_ready"]))[0] is False
                    with pytest.raises(Exception, match="Expected one enabled"):
                        await page.evaluate(call, tool["execute"])
            finally:
                await browser.close()

    asyncio.run(run())
