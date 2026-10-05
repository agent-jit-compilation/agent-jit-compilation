"""Static checking and ranking using the paper's CFG and cost implementation."""

from pathlib import Path
from blastai.agents.models import Agent
from blastai.agents.codecheck import check_code_candidate
from blastai.agents.codecost import compute_code_cost
from .definitions import DefinitionEmitter


def check(path: Path, registry: Path, state: dict):
    agent = Agent.from_smcp_registry(str(registry))
    definitions = DefinitionEmitter(agent)._build_definition_code()
    code = path.read_text()
    valid, error = check_code_candidate(
        code, agent=agent, initial_state=state, definition_code=definitions
    )
    return {
        "path": str(path),
        "valid": valid,
        "error": error or None,
        "estimated_cost": compute_code_cost(code, tools=agent.tools) if valid else None,
        "checks": ["syntax", "types", "tool_state_flow"],
        "scope": "Static checks against declared contracts; not a proof of task correctness or a security sandbox.",
    }


def rank(paths, registry, state):
    candidates = [check(p, registry, state) for p in paths]
    valid = [c for c in candidates if c["valid"]]
    selected = min(valid, key=lambda c: c["estimated_cost"])["path"] if valid else None
    return {
        "selected": selected,
        "candidates": candidates,
        "tied_candidates": [
            c["path"]
            for c in valid
            if selected is not None
            and c["estimated_cost"]
            == next(v["estimated_cost"] for v in valid if v["path"] == selected)
        ],
        "tie_break": "first candidate in input order",
        "cost_units": "heuristic ranking score, not measured seconds",
    }
