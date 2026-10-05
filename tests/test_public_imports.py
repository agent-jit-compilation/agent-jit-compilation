"""Offline imports stay lightweight without removing the recovered public API."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_lazy_packages_preserve_public_names_without_browser_imports():
    script = """
import sys
import blastai, blastai.agents, blast_scheduler, blast_scheduler.offline, blast_scheduler.laminar
assert "browser_use" not in sys.modules
from blastai.agents import Agent, check_code_candidate, compute_code_cost
from blastai.agents.models import Agent as OriginalAgent
from blast_scheduler import TraceStep
from blast_scheduler.views import TraceStep as OriginalTraceStep
assert Agent is OriginalAgent and TraceStep is OriginalTraceStep
assert callable(check_code_candidate) and callable(compute_code_cost)
assert "AgentExecutor" in blastai.agents.__all__
assert "Engine" in blastai.__all__
assert "TraceScraper" in blast_scheduler.laminar.__all__
assert "browser_use" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, timeout=30)


@pytest.mark.skipif(
    importlib.util.find_spec("browser_use") is None, reason="Browser extra required"
)
@pytest.mark.parametrize("script", ["evaluate_codegen.py", "measure_e2e_detailed.py"])
def test_preserved_experiment_help_loads(script):
    result = subprocess.run(
        [sys.executable, f"experiments/{script}", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
