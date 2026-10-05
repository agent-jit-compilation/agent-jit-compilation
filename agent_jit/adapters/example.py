"""A deliberately task-specific, zero-model example using the shared browser page.

This demonstrates the adapter interface; it is not a general agent or a paper baseline.
"""

import json
from . import AgentOutput


async def run(context):
    if context.task["implementation_id"] != "dashdish-custom-5":
        raise ValueError("The example adapter only supports dashdish-custom-5")
    registry = json.loads((context.root / "data/registries/dashdish.json").read_text())
    tool = next(t for t in registry["tools"] if t["name"] == "list_all_stores")
    result = await context.page.evaluate(
        "(inputs) => { " + tool["execute"] + " }", {"detailed": False}
    )
    return AgentOutput(
        json.dumps({"restaurants": [x["name"] for x in result["items"][:3]]})
    )
