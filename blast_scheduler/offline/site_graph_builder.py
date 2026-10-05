import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import openai

from blast_scheduler.laminar.trace_parser import TraceParser
from blast_scheduler.offline.schema import format_elements, list_defs
from blast_scheduler.offline.site import SiteRep
from blast_scheduler.offline.step_processor import process_step
from blast_scheduler.views import StepToElement, TraceStep


class ContextBuilder:
    """Builds and manages page schemas for a site."""

    def __init__(
        self, site_name: Optional[str] = None, openai_client: openai.OpenAI = None,
        schema_root: Optional[str] = None
    ):
        self.site_name = site_name
        self.schema_root = Path(schema_root or "saved_schemas")
        self.client = openai_client or openai.OpenAI()

        # Cache for page schemas
        self._page_schemas: Dict[str, dict] = {}

    @property
    def page_schemas(self) -> Dict[str, dict]:
        """Get all page schemas."""
        return self._page_schemas

    def _get_safe_url(self, url: str) -> str:
        """Convert URL to a safe filename."""
        print(f"Getting safe URL: {url}")
        safe_filename = re.sub(r"^https?://", "", url)
        safe_filename = re.sub(r"[^\w\-_.]", "_", safe_filename)
        safe_filename = safe_filename.strip("_")
        print(f"Safe URL: {safe_filename}")
        return safe_filename

    def _infer_site_name(self, url: str) -> Optional[str]:
        """Infer site name from a URL."""
        try:
            from urllib.parse import urlparse

            domain = urlparse(url).netloc
            domain = domain.replace("www.", "")
            site_name = domain.split(".")[0]
            return site_name
        except Exception:
            return None

    def _strip_page_title(self, url: str) -> str:
        """Strip page title from URL (e.g., 'https://example.com/ - Page Title' -> 'https://example.com/')."""
        # Page titles are typically appended with " - " separator
        if " - " in url:
            return url.split(" - ")[0].strip()
        return url

    def _normalize_url(self, url: str) -> str:
        """Normalize a URL by replacing obvious ids with a placeholder."""
        # First strip the page title if present
        url = self._strip_page_title(url)
        print(f"Normalizing URL: {url}")

        last_part = url.split("/")[-1]
        last_split = last_part.split(":")
        if len(last_split) > 1:
            last_part_new = last_part.replace(last_split[-1], "<id>")
        else:
            last_part_new = last_part
        print(f"New URL: {url.replace(last_part, last_part_new)}")
        return url.replace(last_part, last_part_new)

    def load_page_schemas(
        self,
        urls: List[str],
        site_name: Optional[str] = None,
        url_to_schema: Optional[Dict[str, str]] = None,
    ) -> Dict[str, dict]:
        """Load matching page schemas from disk.

        Args:
            urls: List of URLs to load schemas for
            site_name: Name of the site (folder under saved_schemas/)
            url_to_schema: Optional mapping from URL patterns to schema names.
                If not provided, will try to load from url_mappings.json in the
                schema directory, or fall back to URL-based filename matching.
                Example: {"https://real-gomail.vercel.app/": "main"}

        Returns:
            Dict mapping URLs to their loaded schemas
        """
        site = site_name or self.site_name
        if not site:
            # Try to infer from first URL
            if urls:
                site = self._infer_site_name(urls[0])

        if not site:
            return {}

        schema_dir = self.schema_root / site
        if not schema_dir.exists():
            return {}

        # Load URL mappings from file if not provided
        if url_to_schema is None:
            mappings_file = schema_dir / "url_mappings.json"
            if mappings_file.exists():
                with open(mappings_file, "r") as f:
                    url_to_schema = json.load(f)
                print(f"Loaded URL mappings from {mappings_file}")

        loaded_schemas = {}
        for url in urls:
            # Strip page title from URL for matching purposes
            clean_url = self._strip_page_title(url)
            normalized_url = self._normalize_url(url)
            schema_name = None

            # Try to find a matching schema name from the mapping
            if url_to_schema:
                # Try exact match first (with and without page title)
                if url in url_to_schema:
                    schema_name = url_to_schema[url]
                elif clean_url in url_to_schema:
                    schema_name = url_to_schema[clean_url]
                elif normalized_url in url_to_schema:
                    schema_name = url_to_schema[normalized_url]
                else:
                    # Try regex patterns first (prefixed with "regex:")
                    for pattern, name in url_to_schema.items():
                        if pattern.startswith("regex:"):
                            regex_pattern = pattern[6:]  # Remove "regex:" prefix
                            if (
                                re.match(regex_pattern, url)
                                or re.match(regex_pattern, clean_url)
                                or re.match(regex_pattern, normalized_url)
                            ):
                                schema_name = name
                                break

                    # Try prefix pattern matching - find the LONGEST matching prefix
                    if not schema_name:
                        best_match_len = 0
                        for pattern, name in url_to_schema.items():
                            if pattern.startswith("regex:"):
                                continue  # Skip regex patterns
                            prefix = pattern.rstrip("*")
                            if (
                                url.startswith(prefix)
                                or clean_url.startswith(prefix)
                                or normalized_url.startswith(prefix)
                            ):
                                if len(prefix) > best_match_len:
                                    best_match_len = len(prefix)
                                    schema_name = name

            # Try loading by schema name first
            if schema_name:
                schema_file = schema_dir / f"{schema_name}.json"
                if schema_file.exists():
                    with open(schema_file, "r") as f:
                        schema = json.load(f)
                        loaded_schemas[url] = schema
                        self._page_schemas[url] = schema
                    print(f"Loaded schema '{schema_name}' for {url}")
                    continue

            # Fall back to URL-based filename
            schema_file = (
                schema_dir / f"{self._get_safe_url(normalized_url)}_schema.json"
            )
            print(f"Trying schema file: {schema_file}")
            if schema_file.exists():
                with open(schema_file, "r") as f:
                    schema = json.load(f)
                    loaded_schemas[url] = schema
                    self._page_schemas[url] = schema

        return loaded_schemas

    def get_all_valid_elements(self) -> List[str]:
        """Get valid elements for all pages."""
        if not self._page_schemas:
            self.load_all_page_schemas()

        result = "<valid_elements>\n"
        for url, schema in self._page_schemas.items():
            result += f"{url}:\n{format_elements(list_defs(schema))}\n\n"
        return result + "</valid_elements>"

    def load_all_page_schemas(self) -> Dict[str, dict]:
        """Load all page schemas from disk."""
        schema_dir = self.schema_root / self.site_name
        if not schema_dir.exists():
            return {}
        for schema_file in schema_dir.glob("*.json"):
            if schema_file.stem.endswith("url_mappings"):
                continue
            print(f"Loading schema from {schema_file}")
            with open(schema_file, "r") as f:
                schema = json.load(f)
                self._page_schemas[schema_file.stem] = schema
        return self._page_schemas

    def save_page_schemas(self, site_name: Optional[str] = None) -> None:
        """Save all page schemas to disk."""
        site = site_name or self.site_name
        if not site:
            return

        schema_dir = self.schema_root / site
        schema_dir.mkdir(parents=True, exist_ok=True)

        for url, schema in self._page_schemas.items():
            schema_file = schema_dir / f"{self._get_safe_url(url)}_schema.json"
            with open(schema_file, "w") as f:
                json.dump(schema, f, indent=2)


class TraceProcessor:
    """Processes a single trace to map steps to schema elements."""

    def __init__(
        self, trace_steps: List[TraceStep], openai_client: openai.OpenAI = None
    ):
        self.trace_steps = trace_steps
        self.trace_id = trace_steps[0].trace_id
        self.client = openai_client or openai.OpenAI()

    def map_trace_steps_to_elements(
        self,
        urls_to_schemas: Dict[str, dict],
        max_depth: int,
        max_workers: Optional[int] = 20,
        model: str = "gpt-4.1",
    ) -> List[StepToElement]:
        """
        Map trace steps to schema elements using multiple workers.

        Args:
            urls_to_schemas: Mapping from URLs to their schemas
            max_workers: Maximum number of worker threads (default: 20)
            model: Model to use for processing (default: "gpt-4.1")

        Returns:
            List of StepToElement mappings
        """
        if not urls_to_schemas:
            raise ValueError(
                "No schemas provided. Generate or load schemas first using ContextBuilder."
            )

        def process_step_pair(i: int) -> Optional[tuple[int, StepToElement]]:
            """Process a single step pair and return the index and result."""
            current_step = self.trace_steps[i]
            try:
                step_result = process_step(
                    self.client,
                    step=current_step,
                    urls_to_schemas=urls_to_schemas,
                    max_depth=max_depth,
                    model=model,
                )

                return (
                    i,
                    StepToElement(
                        step_id=step_result.id,
                        element_name=step_result.goal_element,
                        success=step_result.success,
                    ),
                )
            except Exception as e:
                print(f"Warning: Failed to process step {i}: {e}")
                return None

        results = {}

        # Process all steps (last step will have next_step=None)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_step_pair, i): i
                for i in range(len(self.trace_steps))
            }

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    idx, step_to_element = result
                    results[idx] = step_to_element

        # Sort results by index to maintain order
        mappings = [results[i] for i in sorted(results.keys())]

        print(f"Done mapping trace steps to elements for trace {self.trace_id}")
        return mappings

    def to_list(self) -> List[dict]:
        """Convert trace steps to list of dictionaries."""
        return [
            {
                "step": step.step,
                "starting_url": step.starting_url,
                "html_snapshot": step.html_snapshot,
                "thinking": step.thinking,
                "evaluation_previous_goal": step.evaluation_previous_goal,
                "memory": step.memory,
                "next_goal": step.next_goal,
                "next_action_name": step.next_action_name,
                "next_action_args": step.next_action_args,
                "span_id": step.span_id,
                "total_latency": step.total_latency,
                "browser_latency": step.browser_latency,
                "llm_latency": step.llm_latency,
            }
            for step in self.trace_steps
        ]


class MultiTraceProcessor:
    """Process multiple traces from a directory."""

    def __init__(
        self,
        site_name: str,
        traces_directory: str,
        openai_client: Optional[openai.OpenAI] = None,
        processor_model: str = "gpt-4.1",
    ):
        self.site_name = site_name
        self.traces_directory = Path(traces_directory)
        self.client = openai_client or openai.OpenAI()
        self.site = SiteRep(site_name)
        self.processor_model = processor_model
        # Cache for parsed traces
        self._trace_parsers: Dict[str, TraceParser] = {}

    def get_all_urls(self) -> set[str]:
        if not self._trace_parsers:
            self.load_all_traces()
        return set(
            url
            for parser in self._trace_parsers.values()
            for url in parser.unique_page_urls
        )

    def load_all_traces(self) -> Dict[str, TraceParser]:
        """Load all trace files from the directory."""
        if not self.traces_directory.exists():
            raise ValueError(
                f"Traces directory does not exist: {self.traces_directory}"
            )

        trace_files = list(self.traces_directory.glob("*.json"))
        if not trace_files:
            raise ValueError(f"No trace files found in: {self.traces_directory}")

        for trace_file in trace_files:
            trace_id = trace_file.stem
            parser = TraceParser(str(trace_file))
            self._trace_parsers[trace_id] = parser

        return self._trace_parsers

    def process_all_traces(
        self,
        urls_to_schemas: Dict[str, dict],
        max_depth: int = None,
    ) -> SiteRep:
        """
        Process all traces and build step to element mappings.

        Args:
            urls_to_schemas: Mapping from URLs to their schemas
            max_depth: Maximum depth for schema processing
            model: Model to use for processing (default: "gpt-4.1")

        Returns:
            SiteRep with element mappings
        """
        if not self._trace_parsers:
            self.load_all_traces()

        for trace_id, parser in self._trace_parsers.items():
            processor = TraceProcessor(parser.trace_steps, self.client)
            try:
                mappings: List[StepToElement] = processor.map_trace_steps_to_elements(
                    urls_to_schemas,
                    max_depth=max_depth,
                    model=self.processor_model,
                )

                for mapping in mappings:
                    element_name = mapping.element_name
                    step_id = mapping.step_id
                    self.site.add_mapping(element_name, trace_id, [step_id])

            except Exception as e:
                print(f"Warning: Failed to process trace {trace_id}: {e}")
                continue

        return self.site

    def get_trace_parser(self, trace_id: str) -> Optional[TraceParser]:
        return self._trace_parsers.get(trace_id)

    def get_all_trace_ids(self) -> List[str]:
        return list(self._trace_parsers.keys())
