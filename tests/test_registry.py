"""Check custom tool selection without altering source benchmark registries."""

import hashlib
import json
from types import SimpleNamespace

from agent_jit.adapters.registry import load_agent


def test_custom_registry_is_used_and_fingerprinted(tmp_path):
    registry = tmp_path / "custom.json"
    registry.write_text(json.dumps({"description": "fixture", "tools": []}))
    original = registry.read_bytes()
    context = SimpleNamespace(
        root=tmp_path,
        output_dir=tmp_path,
        task={"application": "dashdish", "benchmark": "REAL"},
        configuration={"tool_registry": str(registry)},
    )
    agent = load_agent(context)
    assert agent.tools == []
    evidence = json.loads((tmp_path / "registry-input.json").read_text())
    assert evidence["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert evidence["source"] == str(registry)
    assert registry.read_bytes() == original
