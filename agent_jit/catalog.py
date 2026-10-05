import json
from pathlib import Path
import yaml


def tasks(root: Path):
    data = json.loads((root / "benchmarks/tasks.json").read_text())
    return data["tasks"]


def task(root: Path, task_id: str, catalog_path=None):
    if catalog_path:
        entries = json.loads(Path(catalog_path).read_text())["tasks"]
        matches = [t for t in entries if t["id"] == task_id]
        if len(matches) != 1:
            raise ValueError(f"Task must resolve uniquely in custom catalog: {task_id}")
        entry = dict(matches[0])
        if entry.get("benchmark") == "WebArena":
            from .webarena import SITES, REVISION

            number = entry.get("webarena_task_id")
            if type(number) is not int:
                raise ValueError(
                    "WebArena catalog entries require an integer webarena_task_id"
                )
            raw = json.loads(
                (
                    root / "benchmarks/webarena/overrides/config_files/test.raw.json"
                ).read_text()
            )
            matches = [t for t in raw if t["task_id"] == number]
            if (
                len(matches) != 1
                or len(matches[0]["sites"]) != 1
                or not set(matches[0]["sites"]) <= SITES
            ):
                raise ValueError(
                    "Choose a single-site GitLab or Reddit task from the supplied WebArena catalog"
                )
            config = matches[0]
            entry.update(
                implementation_id=f"webarena-{number}",
                application=config["sites"][0],
                task_details=config["intent"],
                initial_url=config["start_url"],
                benchmark_revision=REVISION,
                verifier={"type": "webarena", "configuration": config},
            )
            return entry
        for key in ("application", "task_details", "initial_url", "verifier"):
            if not entry.get(key):
                raise ValueError(f"Custom task {task_id} requires {key}")
        if entry["verifier"].get("type") != "custom":
            raise ValueError(
                "Custom tasks require verifier.type: custom and callable: module:function"
            )
        if ":" not in entry["verifier"].get("callable", ""):
            raise ValueError("Custom verifier requires callable: module:function")
        entry.setdefault("implementation_id", task_id)
        entry["benchmark"] = "custom"
        entry.setdefault("benchmark_revision", "user-supplied")
        return entry
    matches = [
        t
        for t in tasks(root)
        if t["id"] == task_id or t["implementation_id"] == task_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Task ID must resolve uniquely in the paper subset: {task_id}"
        )
    entry = dict(matches[0])
    path = root / entry["definition_path"]
    definitions = yaml.safe_load(path.read_text())["tasks"]
    definition = next(
        (d for d in definitions if d["id"] == entry["implementation_id"]), None
    )
    if definition is None:
        raise ValueError(
            f"Missing task definition {entry['implementation_id']} in {path}; use a custom task_catalog to add tasks"
        )
    entry["initial_url"] = definition["initial_url"]
    entry["definition"] = definition
    return entry
