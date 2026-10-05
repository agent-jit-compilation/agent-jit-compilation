"""Preprocess Laminar trace data.

We don't make any LLM calls here. This is purely to extract existing data from the trace.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser

from blast_scheduler.views import TraceStep

BASE_SPAN_ID = "00000000-0000-0000-0000-000000000000"


class TraceParser:
    """Parses trace data from Laminar JSON files."""

    def __init__(self, trace_file_path: str, site_name: Optional[str] = None):
        self.trace_file_path = trace_file_path
        self.site_name = site_name
        self.all_spans: List[Dict[str, Any]] = []
        self.span_cache: Dict[str, Dict[str, Any]] = {}
        self.root_span_id: Optional[str] = None
        self.trace_id = Path(trace_file_path).stem

        self._trace_steps_cache: Optional[List[TraceStep]] = None
        self._unique_page_urls_cache: Optional[List[str]] = None
        self._html_snapshots_cache: Optional[Dict[str, str]] = None

        self.load_trace()

    @classmethod
    def output_dir(cls) -> Path:
        return Path("saved_trace_steps")

    @property
    def trace_steps(self) -> List[TraceStep]:
        if self._trace_steps_cache is None:
            self._trace_steps_cache = self.build_trace_steps()
        return self._trace_steps_cache

    @property
    def unique_page_urls(self) -> List[str]:
        if self._unique_page_urls_cache is None:
            self._unique_page_urls_cache = self.get_unique_page_urls()
        return self._unique_page_urls_cache

    @property
    def html_snapshots(self) -> Dict[str, str]:
        if self._html_snapshots_cache is None:
            self._html_snapshots_cache = self.get_html_snapshots()
        return self._html_snapshots_cache

    def load_trace(self) -> None:
        """Load and parse the trace file."""
        with open(self.trace_file_path, "r") as f:
            data = json.load(f)
            self.all_spans = data["spans"]

        # Sort by start time
        self.all_spans.sort(key=lambda x: x["startTime"])

        # Build span cache for quick lookup
        for span in self.all_spans:
            self.span_cache[span["spanId"]] = span

        # Set root span
        if self.all_spans:
            self.root_span_id = self.all_spans[0]["spanId"]

    def get_openai_chat_spans(self) -> List[Dict[str, Any]]:
        """Extract all OpenAI chat spans that are not from execute_action."""
        openai_chat_spans = []
        for span in self.all_spans:
            if span["parentSpanId"] not in self.span_cache:
                continue

            parent_span = self.span_cache[span["parentSpanId"]]
            if (
                span["name"] == "openai.chat"
                and parent_span["name"] != "execute_action"
            ):
                openai_chat_spans.append(span)

        return openai_chat_spans

    def get_html_from_span(self, span: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """Extract HTML content and URL from an OpenAI chat span."""
        assert span["name"] == "openai.chat", "Span must be an openai.chat span"

        user_input = span["input"][-1]["content"]

        # Find current tab URL from text pattern
        tab_url_match = re.search(r"Tab ([A-Z0-9]+): (.*) \-", user_input)
        current_tab_url = tab_url_match.group(2) if tab_url_match else None

        # Find HTML content
        html_parts = user_input.split(
            "Elements you can interact with inside the viewport:"
        )
        if len(html_parts) > 1:
            html_content = html_parts[1].replace("</browser_state>", "")
        else:
            html_content = ""

        return current_tab_url, html_content

    def get_url_from_state(self, state: Dict[str, Any]) -> Optional[str]:
        """Extract URL from state object (new trace format)."""
        # Try direct url field first
        url = state.get("url", "")
        title = state.get("title", "")

        # Fall back to tabs array if no direct url
        if not url:
            tabs = state.get("tabs", [])
            if tabs and len(tabs) > 0:
                tab = tabs[0]
                url = tab.get("url", "")
                title = tab.get("title", "")

        if not url:
            return None

        # Append site name from title for consistency with old format
        # Title is like "Omnizon - Online Shopping..." -> extract "Omnizon"
        if title and " - " not in url:
            site_name = title.split(" - ")[0] if " - " in title else title
            return f"{url} - {site_name}"

        return url

    def get_action_from_span(self, span: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Extract action name and arguments from an OpenAI chat span."""
        assert span["name"] == "openai.chat", "Span must be an openai.chat span"

        try:
            assistant_output = span["output"][0]["content"][0]["text"]
            output_json = json.loads(assistant_output)
            action_name = list(output_json["action"][0].keys())[0]
            action_args = output_json["action"][0][action_name]
            return action_name, action_args
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            # Return empty action if parsing fails
            return ("unknown", {})

    def get_self_eval_from_span(
        self, span: Dict[str, Any]
    ) -> Tuple[str, str, str, str]:
        """Extract self-evaluation data from an OpenAI chat span."""
        assert span["name"] == "openai.chat", "Span must be an openai.chat span"

        try:
            assistant_output = span["output"][0]["content"][0]["text"]
            self_eval = json.loads(assistant_output)

            return (
                self_eval.get("thinking", ""),
                self_eval.get("evaluation_previous_goal", ""),
                self_eval.get("memory", ""),
                self_eval.get("next_goal", ""),
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            # Return empty strings if parsing fails
            return ("", "", "", "")

    def get_parent_span(
        self, span_id: str, parent_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find a parent span with a specific name."""
        if span_id not in self.span_cache:
            return None

        curr_span = self.span_cache[span_id]
        if curr_span["parentSpanId"] not in self.span_cache:
            return None

        parent_span = self.span_cache[curr_span["parentSpanId"]]

        while parent_span["spanId"] != BASE_SPAN_ID:
            if parent_span["name"] == parent_name:
                return parent_span

            if parent_span["parentSpanId"] not in self.span_cache:
                break

            parent_span = self.span_cache[parent_span["parentSpanId"]]

        return None

    def get_latency(self, span_id: str) -> float:
        """Calculate latency for a span in seconds."""
        if span_id not in self.span_cache:
            return 0.0

        span = self.span_cache[span_id]
        start_time = parser.isoparse(span["startTime"])
        end_time = parser.isoparse(span["endTime"])

        return (end_time - start_time).total_seconds()

    def get_unique_page_urls(self) -> List[str]:
        """Get list of unique page URLs visited in this trace."""
        pages = set()
        for step in self.trace_steps:
            if step.starting_url:
                pages.add(step.starting_url)
        return sorted(list(pages))

    def get_html_snapshots(self) -> Dict[str, str]:
        """Get HTML snapshots for each unique page URL."""
        html_snapshots = {}
        for step in self.trace_steps:
            if step.starting_url and step.starting_url not in html_snapshots:
                html_snapshots[step.starting_url] = step.html_snapshot
        return html_snapshots

    def build_trace_steps(self) -> List[TraceStep]:
        """Build trace step objects from trace spans."""
        # First try new format: agent.run span with output.history
        agent_run_steps = self._build_steps_from_agent_run()
        if agent_run_steps:
            return agent_run_steps

        # Fall back to old format: openai.chat spans
        return self._build_steps_from_openai_chat()

    def _build_steps_from_agent_run(self) -> List[TraceStep]:
        """Build trace steps from agent.run span's output.history (new format)."""
        # Find agent.run span
        agent_run_span = None
        for span in self.all_spans:
            if span["name"] == "agent.run":
                agent_run_span = span
                break

        if not agent_run_span:
            return []

        # Check if it has the history format
        output = agent_run_span.get("output", {})
        history = output.get("history", [])
        if not history:
            return []

        trace_steps = []

        for i, step_data in enumerate(history):
            model_output = step_data.get("model_output", {})
            state = step_data.get("state", {})
            metadata = step_data.get("metadata", {})

            # Extract URL from state
            url = self.get_url_from_state(state)

            # Extract action
            actions = model_output.get("action", [])
            if actions and len(actions) > 0:
                action = actions[0]
                action_name = list(action.keys())[0]
                action_args = action[action_name]
            else:
                action_name = "unknown"
                action_args = {}

            # Calculate step latency from metadata if available
            step_start = metadata.get("step_start_time", 0)
            step_end = metadata.get("step_end_time", 0)
            step_latency = step_end - step_start if step_end > step_start else 0

            step = TraceStep(
                trace_id=self.trace_id,
                span_id=f"{agent_run_span['spanId']}-step-{i}",
                step=i,
                starting_url=url or "",
                html_snapshot="",  # Not available in this format
                thinking=model_output.get("thinking", ""),
                evaluation_previous_goal=model_output.get(
                    "evaluation_previous_goal", ""
                ),
                memory=model_output.get("memory", ""),
                next_goal=model_output.get("next_goal", ""),
                next_action_name=action_name,
                next_action_args=action_args,
                total_latency=step_latency,
                browser_latency=0.0,
                llm_latency=step_latency,
            )
            trace_steps.append(step)

        return trace_steps

    def _build_steps_from_openai_chat(self) -> List[TraceStep]:
        """Build trace steps from openai.chat spans (old format)."""
        openai_chat_spans = self.get_openai_chat_spans()
        trace_steps = []

        for i, span in enumerate(openai_chat_spans):
            span_id = span["spanId"]

            # Extract fields
            url, html = self.get_html_from_span(span)
            (
                thinking,
                evaluation_previous_goal,
                memory,
                next_goal,
            ) = self.get_self_eval_from_span(span)
            action_name, action_args = self.get_action_from_span(span)

            # Calculate derived fields
            parent_span = self.get_parent_span(span_id, "agent.step")
            if parent_span:
                total_latency = self.get_latency(parent_span["spanId"])
            else:
                total_latency = 0.0

            llm_latency = self.get_latency(span_id)
            browser_latency = total_latency - llm_latency

            step = TraceStep(
                trace_id=self.trace_id,
                span_id=span_id,
                step=i,
                starting_url=url or "",
                html_snapshot=html,
                thinking=thinking,
                evaluation_previous_goal=evaluation_previous_goal,
                memory=memory,
                next_goal=next_goal,
                next_action_name=action_name,
                next_action_args=action_args,
                total_latency=total_latency,
                browser_latency=browser_latency,
                llm_latency=llm_latency,
            )
            trace_steps.append(step)

        return trace_steps

    def save_trace_steps(self) -> bool:
        """Save trace steps to disk."""
        try:
            trace_steps_dir = self.output_dir()
            trace_steps_dir.mkdir(parents=True, exist_ok=True)
            with open(trace_steps_dir / f"{self.trace_id}.json", "w") as f:
                json.dump([step.to_dict() for step in self.trace_steps], f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving trace steps: {e}")
            return False
