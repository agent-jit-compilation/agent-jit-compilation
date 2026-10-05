"""Read benchmark state from the agent's active tab before adapter cleanup."""

import asyncio
import json
from urllib.parse import urljoin


async def read_final_state(page, task):
    """Preserve tab-local history and allow REAL's state report to settle.

    Both Patchright pages and Browser-Use actor pages expose goto/evaluate.
    A new tab can initialize its own baseline from already-mutated storage,
    losing the action history and differences needed by state verifiers.
    """
    destination = urljoin(
        task["initial_url"], task["verifier"].get("state_url", "finish")
    )
    await page.goto(destination)
    if task["benchmark"] == "REAL":
        # Match AGISDK_TIMEOUT in the historical benchmark state collector.
        # Report generation/rehydration can continue after network-idle.
        await asyncio.sleep(10)

    async def read():
        while True:
            try:
                text = await page.evaluate(
                    "() => {"
                    f"const expected = new URL({json.dumps(destination)});"
                    "const current = new URL(location.href);"
                    "if (current.origin !== expected.origin || "
                    "current.pathname.replace(/[/]$/, '') !== expected.pathname.replace(/[/]$/, '') || "
                    "current.search !== expected.search || document.readyState === 'loading') return '';"
                    "return document.querySelector('pre')?.textContent || ''; }"
                )
            except Exception as exc:
                if not any(
                    message in str(exc)
                    for message in (
                        "Execution context was destroyed",
                        "Cannot find context",
                        "Inspected target navigated or closed",
                    )
                ):
                    raise
                # CDP navigation can return before the replacement document is
                # ready. Retry the read, never the action that produced state.
                text = ""
            if text:
                return json.loads(text)
            await asyncio.sleep(0.1)

    return await asyncio.wait_for(read(), timeout=15)


def state_provider(browser, task):
    """Collect from the current agent tab, including when it opened a new tab."""

    async def collect():
        page = await browser.get_current_page()
        if page is None:
            raise RuntimeError("No active agent tab is available for state evaluation")
        return await read_final_state(page, task)

    return collect
