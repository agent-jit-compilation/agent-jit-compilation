"""Pinned WebArena source preparation, configuration, and native evaluation.

The benchmark evaluator is imported unchanged from the saved local overrides.
Preparation does not start services, download images, or make model calls.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REVISION = "dce04686a56253aefba7b18a4fa0937cf1dc987b"
REPOSITORY = "https://github.com/web-arena-x/webarena.git"
SITES = {"gitlab", "reddit"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(root: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError(
            "Use a new directory; preparation does not overwrite a checkout"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(
        ["git", "fetch", "--depth=1", REPOSITORY, REVISION],
        cwd=destination,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination, check=True
    )
    overrides = json.loads((root / "benchmarks/webarena/overrides.json").read_text())
    for entry in overrides:
        source = root / "benchmarks/webarena/overrides" / entry["path"]
        if digest(source) != entry["sha256"]:
            raise ValueError(f"Saved override checksum changed: {entry['path']}")
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return verify(root, destination)


def verify(root: Path, checkout: Path):
    checkout = checkout.resolve()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    if revision != REVISION:
        raise ValueError(f"WebArena must be at {REVISION}")
    overrides = json.loads((root / "benchmarks/webarena/overrides.json").read_text())
    paths = {e["path"] for e in overrides}
    changed = (
        subprocess.check_output(
            ["git", "diff", "HEAD", "--name-only", "-z"], cwd=checkout
        )
        .decode()
        .split("\0")
    )
    if set(filter(None, changed)) - paths:
        raise ValueError("WebArena checkout has unrecorded source changes")
    for entry in overrides:
        if digest(checkout / entry["path"]) != entry["sha256"]:
            raise ValueError(f"Missing or changed WebArena override: {entry['path']}")
    # Untracked Python modules could shadow the checked-in evaluator.
    untracked = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=checkout
        )
        .decode()
        .split("\0")
    )
    if any(p.endswith(".py") and p not in paths for p in untracked):
        raise ValueError("WebArena checkout contains untracked Python source")
    return {
        "repository": REPOSITORY,
        "revision": revision,
        "checkout": str(checkout),
        "overrides": overrides,
    }


def resolve_config(task, endpoints):
    """Replace URL placeholders recursively, without changing task IDs or assertions."""
    config = task["verifier"]["configuration"]
    for site in config["sites"]:
        if site not in SITES or not endpoints.get(site):
            raise ValueError(f"Missing supported WebArena endpoint: {site}")

    def replace(value):
        if isinstance(value, str):
            for site, url in endpoints.items():
                value = value.replace(f"__{site.upper()}__", url.rstrip("/"))
            if "__GITLAB__" in value or "__REDDIT__" in value:
                raise ValueError("Unresolved WebArena URL placeholder")
            return value
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        return value

    return replace(config)


def load_evaluator(checkout, endpoints):
    """Call in a dedicated process; original modules bind endpoints at import time."""
    sys.path.insert(0, str(Path(checkout).resolve()))
    for key in (
        "GITLAB",
        "REDDIT",
        "SHOPPING",
        "SHOPPING_ADMIN",
        "WIKIPEDIA",
        "MAP",
        "HOMEPAGE",
    ):
        # Unused services cannot accidentally resolve to a real benchmark endpoint.
        os.environ[key] = endpoints.get(key.lower(), "http://127.0.0.1:1")
    from evaluation_harness import evaluator_router

    return evaluator_router


def validate_evaluation(config):
    """Validate the supported evaluator subset before any environment starts."""
    if set(config["eval"]["eval_types"]) - {
        "string_match",
        "program_html",
        "url_match",
    }:
        raise ValueError("Unsupported WebArena evaluator type")
    if set((config["eval"].get("reference_answers") or {})) - {
        "exact_match",
        "must_include",
    }:
        raise ValueError(
            "WebArena fuzzy_match/infeasible-task evaluation is not supported by this runner, even with llm_judge. Choose an exact_match/must_include or DOM task; the fallback judge only upgrades these native string checks."
        )


def evaluate_native(
    checkout,
    endpoints,
    config_file,
    text,
    cdp_url=None,
    llm_judge=False,
    ledger_path=None,
    budget=1,
    max_requests=128,
):
    """Run native checks and, when selected, the original fallback judge."""
    config = json.loads(Path(config_file).read_text())
    validate_evaluation(config)
    router = load_evaluator(checkout, endpoints)
    evaluator = router(str(config_file))
    trajectory = [{"answer": text}]
    if config["eval"]["eval_types"] == ["string_match"]:
        score = evaluator(trajectory, str(config_file), None)
    else:
        if not cdp_url:
            raise ValueError(
                "Browser-based WebArena evaluation requires the live browser"
            )
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            try:
                pages = [p for ctx in browser.contexts for p in ctx.pages]
                if not pages:
                    raise ValueError("No live page for evaluation")
                score = evaluator(trajectory, str(config_file), pages[-1])
            finally:
                browser.close()  # Disconnect this CDP client; owner closes its browser.
    native_score = float(score)
    judge = None
    if llm_judge and score < 1 and config["eval"]["eval_types"] == ["string_match"]:
        if ledger_path is None:
            raise ValueError("WebArena LLM judge requires a shared budget ledger")
        import asyncio
        from .budget import Ledger
        from .webarena_judge import judge_answer

        answers = config["eval"].get("reference_answers", {})
        if answers:
            try:
                judge = asyncio.run(
                    judge_answer(
                        text,
                        answers,
                        config.get("intent", ""),
                        Ledger(Path(ledger_path), budget, max_requests),
                    )
                )
                score = max(score, judge["score"])
            except Exception as exc:
                judge = {"error_type": type(exc).__name__, "used_native_score": True}
    return {
        "correct": score == 1,
        "score": float(score),
        "mode": "webarena_historical_judge"
        if llm_judge
        else "webarena_native_saved_overrides_no_llm_upgrade",
        "native_score": native_score,
        "judge": judge,
        "revision": REVISION,
    }


if __name__ == "__main__":
    req = json.loads(Path(sys.argv[1]).read_text())
    result = evaluate_native(**req)
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + "\n")
