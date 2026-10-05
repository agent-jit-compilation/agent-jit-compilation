"""Load the original tool registry with explicit local benchmark endpoint overrides."""

import hashlib
import json
from pathlib import Path


def load_agent(context, use_tools=True):
    """Load and record the exact registry used by one planner/baseline attempt."""
    from blastai.agents.models import Agent

    if not use_tools:
        return Agent(description="", tools=[])
    registry = Path(
        context.configuration.get("tool_registry")
        or context.root / f"data/registries/{context.task['application']}.json"
    )
    source = registry.resolve()
    source_hash = hashlib.sha256(registry.read_bytes()).hexdigest()
    if context.task["benchmark"] == "WebArena":
        original = registry.read_text()
        for site, origin in {
            "gitlab": "http://localhost:8023",
            "reddit": "http://localhost:9999",
        }.items():
            endpoint = (
                context.configuration.get("endpoints", {}).get(site, origin).rstrip("/")
            )
            original = original.replace(
                origin.removeprefix("http://"), endpoint.removeprefix("http://")
            )
        registry = context.output_dir / "resolved-registry.json"
        registry.write_text(original)
    agent = Agent.from_smcp_registry(str(registry))
    (context.output_dir / "registry-input.json").write_text(
        json.dumps(
            {
                "source": str(source),
                "source_sha256": source_hash,
                "resolved_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
                "tool_names": [tool.name for tool in agent.tools],
            },
            indent=2,
        )
        + "\n"
    )
    return agent
