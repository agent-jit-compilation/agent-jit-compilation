"""
Page Schema Generation Module

Generates JSON schemas with element descriptions from screenshots and HTML snapshots.

Usage:
    from blast_scheduler.page_schema import PageSchemaGenerator

    generator = PageSchemaGenerator(client)
    schemas = generator.generate_from_folder("path/to/pages")
    generator.save_schemas(schemas, "saved_schemas/site_name")
"""

from blast_scheduler.page_schema.generator import PageSchemaGenerator
from blast_scheduler.page_schema.prompts import HTML_SECTION, SCHEMA_PROMPT

__all__ = [
    "PageSchemaGenerator",
    "SCHEMA_PROMPT",
    "HTML_SECTION",
]
