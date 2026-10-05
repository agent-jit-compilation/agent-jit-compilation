"""Test the original trace parser, labeling prompt, and profile handoff."""

import json
from types import SimpleNamespace

import pytest

from agent_jit.profiles import build_profile
from agent_jit.traces import label_traces, parse_traces
from blast_scheduler.views import Step


def test_parse_label_fit_pipeline_and_failed_label_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "trace1.json").write_text(
        json.dumps(
            {
                "spans": [
                    {
                        "spanId": "root",
                        "parentSpanId": "0",
                        "name": "agent.run",
                        "startTime": "2026-01-01T00:00:00Z",
                        "endTime": "2026-01-01T00:00:05Z",
                        "output": {
                            "history": [
                                {
                                    "state": {"url": "https://example.test/"},
                                    "model_output": {
                                        "next_goal": "Press submit",
                                        "action": [{"click": {"index": 1}}],
                                    },
                                    "metadata": {
                                        "step_start_time": 1,
                                        "step_end_time": 4,
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        )
    )
    parsed = tmp_path / "parsed"
    report = parse_traces(raw, parsed)
    assert report["steps"] == {"trace1": 1} and report["model_calls"] == 0
    schema_root = tmp_path / "schemas"
    app = schema_root / "fixture"
    app.mkdir(parents=True)
    (app / "url_mappings.json").write_text(
        json.dumps({"https://example.test/": "main"})
    )
    (app / "main.json").write_text(
        json.dumps({"$defs": {"Submit": {"description": "Submit form"}}})
    )
    responses = [
        Step(
            id=0,
            goal_element="nonexistent",
            success=False,
            is_modal=False,
            modal_name="",
        ),
        Step(id=0, goal_element="Submit", success=False, is_modal=False, modal_name=""),
    ]
    calls = []
    closed = []

    def parse(**kwargs):
        calls.append(json.dumps(kwargs["input"]))
        label = responses.pop(0)
        return SimpleNamespace(
            output_parsed=label,
            output_text=label.model_dump_json(),
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
        )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(parse=parse), close=lambda: closed.append(True)
        ),
    )
    output = tmp_path / "labels"
    report = label_traces(
        parsed / "steps",
        schema_root,
        "fixture",
        output,
        "gpt-4.1",
        tmp_path / "ledger.json",
        1,
    )
    assert report["status"] == "success" and len(calls) == 2 and closed
    assert "Invalid response" in calls[1]
    assert not report["labels"]["trace1"][0][
        "success"
    ]  # Original mappings retain unsuccessful steps.
    fitted = build_profile(
        output / "site_rep.json",
        parsed / "steps",
        tmp_path / "profile",
        "fixture-model",
    )
    assert fitted["observations"] == {"Submit": 1}
    profile = json.loads((tmp_path / "profile/element_latencies.json").read_text())
    assert profile["element_latencies"]["Submit"]["values"] == [3]

    responses.append(
        Step(id=42, goal_element="Submit", success=True, is_modal=False, modal_name="")
    )
    failed = tmp_path / "failed"
    with pytest.raises(ValueError, match="mismatched label"):
        label_traces(
            parsed / "steps",
            schema_root,
            "fixture",
            failed,
            "gpt-4.1",
            tmp_path / "ledger.json",
            1,
        )
    assert not (failed / "site_rep.json").exists()
    assert json.loads((failed / "build.json").read_text())["status"] == "failed"


def test_empty_trace_export_is_not_a_successful_parse(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "empty.json").write_text(json.dumps({"spans": []}))
    with pytest.raises(ValueError, match="No supported agent steps"):
        parse_traces(raw, tmp_path / "parsed")
    assert (
        json.loads((tmp_path / "parsed/build.json").read_text())["status"] == "failed"
    )


def test_native_history_export_and_parse_reject_unmeasured_steps(tmp_path):
    from agent_jit.history import save_history

    trial = tmp_path / "trial-id"
    native = {
        "history": [
            {
                "state": {"title": "Initial Actions"},
                "metadata": None,
                "model_output": None,
            },
            {
                "state": {"url": "https://example.test/", "title": "Inbox"},
                "metadata": {"step_start_time": 10, "step_end_time": 13},
                "model_output": {
                    "next_goal": "Create label",
                    "action": [{"click": {"index": 2}}],
                },
            },
            {
                "state": {"url": "https://example.test/"},
                "metadata": {"step_start_time": 13, "step_end_time": 17},
                "model_output": None,
                "result": [{"error": "model failed"}],
            },
        ]
    }
    save_history(SimpleNamespace(model_dump=lambda: native), trial, "A")
    raw = trial / "browser-histories"
    saved = raw / "trial-id-A.json"
    assert json.loads(saved.read_text()) == native
    parsed = tmp_path / "parsed"
    report = parse_traces(raw, parsed, "browser-history")
    assert report["history_mapping"]["trial-id-A"] == {
        "source_step_indices": [1, 2],
        "excluded_initial_action_indices": [0],
    }
    steps = json.loads((parsed / "steps/trial-id-A.json").read_text())
    assert [s["total_latency"] for s in steps] == [3, 4]
    assert [s["step"] for s in steps] == [0, 1]
    assert steps[1]["next_action_name"] == "unknown"  # Failed model step is retained.
    native["history"][1]["metadata"] = None
    save_history(SimpleNamespace(model_dump=lambda: native), trial, "A")
    with pytest.raises(ValueError, match="measured timing"):
        parse_traces(raw, tmp_path / "bad", "browser-history")
    assert json.loads((tmp_path / "bad/build.json").read_text())["status"] == "failed"
    with pytest.raises(ValueError, match="basename"):
        save_history(None, trial, "../escape")


def test_supplied_schemas_cover_real_benchmark_hosts_and_routes():
    from pathlib import Path
    from urllib.parse import urljoin
    from agent_jit.catalog import task
    from blast_scheduler.offline.site_graph_builder import ContextBuilder

    root = Path(__file__).resolve().parents[1]
    routes = {
        "dashdish": ("store/1", "store"),
        "gomail": ("", "main"),
        "omnizon": ("orders", "returns_and_orders"),
    }
    for app, (route, expected) in routes.items():
        item = task(root, f"{app}-custom-1")
        url = urljoin(item["initial_url"], route)
        context = ContextBuilder(
            app,
            openai_client=object(),
            schema_root=str(root / "data/scheduler/saved_schemas"),
        )
        schemas = context.load_page_schemas([url])
        assert url in schemas
        # Route-specific mappings must win over the homepage prefix.
        expected_schema = json.loads(
            (root / f"data/scheduler/saved_schemas/{app}/{expected}.json").read_text()
        )
        assert schemas[url] == expected_schema
