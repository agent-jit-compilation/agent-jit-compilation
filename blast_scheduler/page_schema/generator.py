"""
Page Schema Generator

Generates JSON schemas from screenshots of webpages.
"""

import base64
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

import openai

from blast_scheduler.page_schema.prompts import (
    EXISTING_DEFS_SECTION,
    HTML_SECTION,
    KNOWN_PAGES_SECTION,
    MODAL_HINT_SECTION,
    SCHEMA_PROMPT,
)


class PageSchemaGenerator:
    """Generates JSON schemas from webpage screenshots and optional HTML snapshots."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    HTML_EXTENSIONS = {".txt", ".html", ".htm"}

    def __init__(
        self,
        client: Optional[openai.OpenAI] = None,
        model: str = "gpt-5.1",
    ):
        """Initialize the schema generator.

        Args:
            client: OpenAI client. If None, creates a new client.
            model: Model to use for schema generation (must support vision)
        """
        self.client = client or openai.OpenAI()
        self.model = model

    def generate(
        self,
        url: str,
        image_paths: List[str],
        existing_defs: Optional[dict] = None,
        html_paths: Optional[List[str]] = None,
        known_pages: Optional[List[str]] = None,
        modal_hints: Optional[List] = None,
    ) -> dict:
        """Generate schema from one or more screenshots and optional HTML snapshots.

        Args:
            url: URL of the webpage
            image_paths: List of paths to screenshot images
            existing_defs: Optional dict of existing $defs to reuse
            html_paths: Optional list of paths to HTML/accessibility tree snapshots
            known_pages: Optional list of known page names for opensPage references
            modal_hints: Optional list of modal hints. Each can be a string (modal name)
                or dict with "name" and optional "description" keys.

        Returns:
            Generated JSON schema
        """
        if not image_paths:
            raise ValueError("At least one image path is required")

        # Build image content list with filenames as context
        image_contents = []
        for image_path in image_paths:
            image_data = self._load_image_as_base64(image_path)
            image_ext = Path(image_path).suffix.lower()
            media_type = self._get_media_type(image_ext)
            image_name = Path(image_path).name

            # Add filename as text context before the image
            image_contents.append(
                {
                    "type": "text",
                    "text": f"--- {image_name} ---",
                }
            )
            image_contents.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{image_data}"},
                }
            )

        plural = "s" if len(image_paths) > 1 else ""

        # Format existing defs section if provided
        if existing_defs:
            existing_defs_str = json.dumps(existing_defs, indent=2)
            existing_defs_section = EXISTING_DEFS_SECTION.format(
                existing_defs=existing_defs_str
            )
        else:
            existing_defs_section = ""

        # Format HTML section if provided
        if html_paths:
            html_contents = []
            for i, html_path in enumerate(html_paths):
                html_text = self._load_html_file(html_path)
                html_name = Path(html_path).name
                html_contents.append(f"--- {html_name} ---\n{html_text}")
            html_content = "\n\n".join(html_contents)
            html_section = HTML_SECTION.format(html_content=html_content)
        else:
            html_section = ""

        # Format known pages section if provided
        if known_pages:
            known_pages_str = ", ".join(f'"{p}"' for p in known_pages)
            known_pages_section = KNOWN_PAGES_SECTION.format(
                known_pages=known_pages_str
            )
        else:
            known_pages_section = ""

        # Format modal hint section from modals.json if provided
        modal_hint_section = ""
        if modal_hints:
            modal_list_lines = []
            for modal in modal_hints:
                if isinstance(modal, dict):
                    name = modal.get("name", "")
                    desc = modal.get("description", "")
                    modal_list_lines.append(
                        f"  - {name}: {desc}" if desc else f"  - {name}"
                    )
                else:
                    modal_list_lines.append(f"  - {modal}")
            modal_hint_section = MODAL_HINT_SECTION.format(
                modal_count=len(modal_hints),
                modal_list="\n".join(modal_list_lines),
            )

        prompt_text = SCHEMA_PROMPT.format(
            url=url,
            plural=plural,
            existing_defs_section=existing_defs_section,
            html_section=html_section,
            known_pages_section=known_pages_section,
            modal_hint_section=modal_hint_section,
        )

        user_content = [{"type": "text", "text": prompt_text}] + image_contents

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": user_content}],
            response_format={"type": "json_object"},
        )

        return json.loads(resp.choices[0].message.content)

    def generate_from_folder(
        self,
        folder_path: str,
        url: str,
        existing_defs: Optional[dict] = None,
        html_folder_path: Optional[str] = None,
        known_pages: Optional[List[str]] = None,
    ) -> dict:
        """Generate schema from all images in a folder, with optional HTML snapshots.

        Args:
            folder_path: Path to folder containing screenshot images
            url: URL of the webpage
            existing_defs: Optional dict of existing $defs to reuse
            html_folder_path: Optional path to folder containing HTML/accessibility
                tree snapshots. If None, no HTML is included. If a modals.json file
                exists in this folder, it will be used for modal hints.
            known_pages: Optional list of known page names for opensPage references

        Returns:
            Generated JSON schema
        """
        folder = Path(folder_path)
        if not folder.exists():
            raise ValueError(f"Folder does not exist: {folder_path}")

        image_paths = [
            str(f)
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in self.IMAGE_EXTENSIONS
        ]

        if not image_paths:
            raise ValueError(f"No image files found in: {folder_path}")

        # Load HTML files and modals.json if html_folder_path is provided
        html_paths = None
        modal_hints = None
        if html_folder_path:
            html_folder = Path(html_folder_path)
            if html_folder.exists():
                html_paths = [
                    str(f)
                    for f in html_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in self.HTML_EXTENSIONS
                ]
                if html_paths:
                    print(f"Found {len(html_paths)} HTML snapshots to include")

                # Load modals.json if it exists
                modals_file = html_folder / "modals.json"
                if modals_file.exists():
                    with open(modals_file, "r", encoding="utf-8") as f:
                        modals_data = json.load(f)
                        modal_hints = modals_data.get("modals", modals_data)
                        if isinstance(modal_hints, list):
                            print(
                                f"Found {len(modal_hints)} modal hints in modals.json"
                            )

        print(f"Found {len(image_paths)} images to process")
        return self.generate(
            url,
            image_paths,
            existing_defs=existing_defs,
            html_paths=html_paths,
            known_pages=known_pages,
            modal_hints=modal_hints,
        )

    def generate_site_schemas(
        self,
        pages: List[dict],
        output_dir: Optional[str] = None,
    ) -> dict:
        """Generate schemas for all pages of a site, accumulating $defs across pages.

        Args:
            pages: List of page configs, each with:
                - "url": URL of the page (required)
                - "image_folder": Path to folder with screenshots (required)
                - "output_name": Custom output filename without extension (optional)
                - "html_folder": Path to folder with HTML snapshots (optional)
            output_dir: Optional directory to save schemas. If provided, saves each
                schema to {output_dir}/{output_name}.json

        Returns:
            Dict mapping URLs to their generated schemas
        """
        accumulated_defs = {}
        schemas = {}

        # Collect all known page names upfront for opensPage references
        known_pages = []
        for page in pages:
            output_name = page.get("output_name")
            if output_name:
                known_pages.append(output_name)
            else:
                # Derive from URL if no output_name provided
                safe_name = self._get_safe_filename(
                    page["url"].split("/")[-1] or "index"
                )
                known_pages.append(safe_name)

        print(f"Known pages for opensPage: {known_pages}")

        for i, page in enumerate(pages):
            url = page["url"]
            image_folder = page["image_folder"]
            output_name = page.get("output_name")
            html_folder = page.get("html_folder")

            print(f"\n[{i + 1}/{len(pages)}] Generating schema for: {url}")

            schema = self.generate_from_folder(
                image_folder,
                url,
                existing_defs=accumulated_defs if accumulated_defs else None,
                html_folder_path=html_folder,
                known_pages=known_pages,
            )

            # Merge new $defs into accumulated set
            new_defs = schema.get("$defs", {})
            accumulated_defs.update(new_defs)
            print(
                f"  -> {len(new_defs)} $defs (total accumulated: {len(accumulated_defs)})"
            )

            schemas[url] = schema

            # Save if output_dir is provided
            if output_dir:
                if output_name:
                    safe_name = output_name
                else:
                    safe_name = self._get_safe_filename(url.split("/")[-1] or "index")
                output_path = Path(output_dir) / f"{safe_name}.json"
                self.save_schema(schema, str(output_path))

        return schemas

    def save_schema(
        self,
        schema: dict,
        output_path: str,
    ) -> None:
        """Save a generated schema to a file.

        Args:
            schema: The schema to save
            output_path: Path to save the schema to
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "w") as f:
            json.dump(schema, f, indent=2)

        print(f"Saved: {output}")

    def load_schema(self, schema_path: str) -> dict:
        """Load a schema from a file.

        Args:
            schema_path: Path to the schema JSON file

        Returns:
            The loaded schema
        """
        with open(schema_path, "r") as f:
            return json.load(f)

    def validate_schema(self, schema: dict) -> Tuple[bool, List[str]]:
        """Validate that all elements in a schema have required fields.

        Args:
            schema: JSON schema to validate

        Returns:
            Tuple of (is_valid, list of issues)
        """
        issues = []

        defs = schema.get("$defs", {})
        if not defs:
            issues.append("Schema has no $defs")
        else:
            # Check that each $def has a description
            for def_name, def_value in defs.items():
                if isinstance(def_value, dict) and not def_value.get("description"):
                    issues.append(f"$def '{def_name}' is missing a description")

        if not schema.get("$id"):
            issues.append("Schema has no $id")

        if not schema.get("title"):
            issues.append("Schema has no title")

        return len(issues) == 0, issues

    def _load_image_as_base64(self, image_path: str) -> str:
        """Load an image file and return as base64 string."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _load_html_file(self, html_path: str) -> str:
        """Load an HTML/accessibility tree snapshot file and return its content."""
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()

    def _get_media_type(self, extension: str) -> str:
        """Get MIME type for image extension."""
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return media_types.get(extension, "image/png")

    def _get_safe_filename(self, name: str) -> str:
        """Convert a name to a safe filename."""
        safe_name = re.sub(r"[^\w\-_.]", "_", name)
        safe_name = safe_name.strip("_")
        return safe_name
