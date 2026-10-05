"""Build scheduler page schemas with the original extraction prompt.

Inputs are user-supplied screenshots and optional HTML snapshots. This stage
does not crawl a site, label traces, or synthesize executable planner tools.
"""

import contextlib
import io
import hashlib
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from unittest.mock import patch
from string import Formatter
from blast_scheduler.page_schema.prompts import SCHEMA_PROMPT

from jsonschema import Draft202012Validator

from agent_jit.browser_model import bounded_payload
from agent_jit.budget import Ledger, RATES
from agent_jit.model_catalog import provider, require_credentials
from blast_scheduler.page_schema.generator import PageSchemaGenerator


class SchemaClient:
    """Adapt synchronous schema extraction to the shared spending ledger."""

    def __init__(self, model: str, ledger: Ledger, max_output_tokens: int):
        from openai import OpenAI

        if model not in RATES:
            raise ValueError("Unsupported schema model price bound")
        if not 1 <= max_output_tokens <= 32768:
            raise ValueError("Output token limit must be in 1..32768")
        self.model = model
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens
        require_credentials(model)
        self.client = (
            None
            if provider(model) != "openai"
            else OpenAI(
                max_retries=0, timeout=120, base_url="https://api.openai.com/v1"
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        """Reserve a single vision request and retain uncertain charges."""
        if kwargs.get("model") != self.model:
            raise ValueError("Schema model differs from the configured model")
        if provider(self.model) != "openai":
            from .native_model import invoke_structured

            response = invoke_structured(
                self.model,
                self.ledger,
                kwargs["messages"],
                None,
                self.max_output_tokens,
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=response.completion),
                    )
                ]
            )
        if self.model.startswith("gpt-5"):
            kwargs.pop("temperature", None)
        kwargs["max_completion_tokens"] = self.max_output_tokens
        kwargs["service_tier"] = "default"
        request_id = self.ledger.reserve(
            self.model, bounded_payload(kwargs), self.max_output_tokens
        )
        try:
            response = self.client.chat.completions.create(**kwargs)
        except BaseException:
            self.ledger.finish(request_id, status="request_failed_charge_unknown")
            raise
        self.ledger.finish(
            request_id,
            response.usage.model_dump() if response.usage else None,
            status="complete" if response.usage else "complete_usage_unavailable",
        )
        if (
            not response.choices
            or response.choices[0].finish_reason != "stop"
            or not response.choices[0].message.content
        ):
            raise ValueError("Schema response was incomplete or refused")
        return response

    def close(self):
        """Release the provider connection."""
        if self.client is not None:
            self.client.close()


class ValidatedGenerator(PageSchemaGenerator):
    """Validate generated schemas before publishing them to the cache folder."""

    def save_schema(self, schema: dict, output_path: str) -> None:
        Draft202012Validator.check_schema(schema)
        if not isinstance(schema.get("$defs"), dict) or not schema["$defs"]:
            raise ValueError("A page schema must define actionable elements in $defs")
        super().save_schema(schema, output_path)


def load_pages(config: Path) -> tuple[list[dict], list[Path]]:
    """Resolve and validate page inputs before any model calls.

    Args:
        config: JSON list of original generator page configurations. Relative
            screenshot and HTML folders are resolved against this file.

    Returns:
        Resolved page configurations and the input files to fingerprint.

    Raises:
        ValueError: If pages, names, URLs, or screenshot inputs are invalid.
        FileNotFoundError: If a configured input directory is missing.
    """
    pages = json.loads(config.read_text())
    if not isinstance(pages, list) or not pages:
        raise ValueError("Page configuration must be a nonempty JSON list")
    names, urls = set(), set()
    inputs = [config.resolve()]
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Each page must be a JSON object")
        name = page.get("output_name", "")
        url = page.get("url", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in {
            "config",
            "url_mappings",
            "build",
        }:
            raise ValueError("Use a unique, plain page name such as main or checkout")
        if name in names or url in urls or not url.startswith(("http://", "https://")):
            raise ValueError("Page names and HTTP(S) URLs must be unique")
        names.add(name)
        urls.add(url)
        for key in ("image_folder", "html_folder"):
            if key == "html_folder" and not page.get(key):
                continue
            directory = (config.parent / page[key]).resolve()
            if not directory.is_dir():
                raise FileNotFoundError(directory)
            page[key] = str(directory)
            extensions = (
                PageSchemaGenerator.IMAGE_EXTENSIONS
                if key == "image_folder"
                else PageSchemaGenerator.HTML_EXTENSIONS
            )
            selected = sorted(
                p
                for p in directory.iterdir()
                if p.is_file()
                and (p.suffix.lower() in extensions or p.name == "modals.json")
            )
            if key == "image_folder" and not any(
                p.suffix.lower() in extensions for p in selected
            ):
                raise ValueError(f"No screenshots found for page {name}")
            inputs.extend(selected)
    return pages, inputs


def build_schemas(
    config: Path,
    output: Path,
    model: str,
    ledger_path: Path,
    budget_usd: float,
    max_output_tokens: int = 8192,
    *,
    prompt_file: Path | None = None,
    max_requests: int = 128,
) -> dict:
    """Generate page schemas and loader configuration in a new directory.

    This is paid offline preprocessing. Partial output and a failure record are
    retained if generation fails; only a successful build writes loader config.
    The caller must review element names and navigation before labeling traces.
    """
    template = prompt_file.read_text() if prompt_file else SCHEMA_PROMPT
    required = {
        "url",
        "plural",
        "existing_defs_section",
        "html_section",
        "known_pages_section",
        "modal_hint_section",
    }
    fields = {
        field for _, field, _, _ in Formatter().parse(template) if field is not None
    }
    if fields != required:
        raise ValueError(
            f"Schema prompt must preserve exactly these placeholders: {', '.join(sorted(required))}; escape literal JSON braces as {{{{ and }}}}"
        )
    template.format(**{field: "" for field in required})
    if provider(model) == "groq":
        raise ValueError(
            "Page schema extraction requires a vision model; GPT-OSS is text only"
        )
    pages, inputs = load_pages(config)
    if prompt_file:
        inputs.append(prompt_file.resolve())
    if output.exists():
        raise ValueError(
            "Output directory already exists; choose a new cache directory"
        )
    input_hashes = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(set(inputs))
    ]
    ledger = Ledger(ledger_path, budget_usd, max_requests)
    client = SchemaClient(model, ledger, max_output_tokens)
    output.mkdir(parents=True)
    start = time.perf_counter()
    report = {
        "schema_version": "1.0",
        "kind": "offline_page_schema_build",
        "model": model,
        "prompt_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "inputs": input_hashes,
        "ledger": str(ledger_path),
        "status": "failed",
        "pages": [p["output_name"] for p in pages],
    }
    (output / "schema-prompt.txt").write_text(template)
    diagnostics = io.StringIO()
    try:
        generator = ValidatedGenerator(client=client, model=model)
        with (
            contextlib.redirect_stdout(diagnostics),
            patch("blast_scheduler.page_schema.generator.SCHEMA_PROMPT", template),
        ):
            generator.generate_site_schemas(pages, str(output))
        (output / "config.json").write_text(
            json.dumps({"page_order": report["pages"]}, indent=2) + "\n"
        )
        (output / "url_mappings.json").write_text(
            json.dumps({p["url"]: p["output_name"] for p in pages}, indent=2) + "\n"
        )
        report["status"] = "success"
        return report
    finally:
        report["offline_s"] = time.perf_counter() - start
        (output / "build.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "build.log").write_text(diagnostics.getvalue())
        client.close()
