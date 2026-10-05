"""Capture a page's screenshot and HTML for build-schemas; no model calls."""

import argparse
import asyncio
import json
from pathlib import Path
import re


async def capture(url, output, name="main", interactive=False, wait_for=None):
    from patchright.async_api import async_playwright

    if not url.startswith(("http://", "https://")) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", name
    ):
        raise ValueError("Provide an HTTP(S) URL and a plain page name")
    if output.exists():
        raise ValueError("Choose a new output directory")
    output.mkdir(parents=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not interactive)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(url, wait_until="domcontentloaded")
            if interactive:
                await asyncio.to_thread(
                    input,
                    "Navigate or open the relevant menu/modal, then press Enter here to capture: ",
                )
            if wait_for:
                await page.locator(wait_for).wait_for(state="visible")
            await page.screenshot(path=str(output / "page.png"), full_page=True)
            (output / "page.html").write_text(await page.content())
            config = [
                {
                    "url": page.url,
                    "image_folder": ".",
                    "html_folder": ".",
                    "output_name": name,
                }
            ]
            (output / "pages.json").write_text(json.dumps(config, indent=2) + "\n")
            return {"output": str(output.resolve()), "url": page.url, "model_calls": 0}
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default="main")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--wait-for", help="CSS selector that must be visible before capture"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                capture(
                    args.url, args.output, args.name, args.interactive, args.wait_for
                )
            ),
            indent=2,
        )
    )
