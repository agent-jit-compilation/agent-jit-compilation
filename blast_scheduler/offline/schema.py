import json

import openai

from blast_scheduler.prompts import (
    SYSTEM_PROMPT_PRODUCE_JSON_SCHEMA,
    USER_PROMPT_PRODUCE_JSON_SCHEMA,
)


def produce_json_schema(client: openai.OpenAI, url: str, html: str):
    """
    Produce a JSON Schema that summarizes the given HTML.
    """

    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_PRODUCE_JSON_SCHEMA},
            {
                "role": "user",
                "content": USER_PROMPT_PRODUCE_JSON_SCHEMA.format(url=url, html=html),
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def format_page_information(all_page_schemas):
    output = ""
    i = 0
    for url, schema in all_page_schemas.items():
        output += f"Page {i}: {schema['$id']}\n"
        output += f"url: {url}\n"
        output += f"{schema}\n"
        output += "\n"
        i += 1
    return output


def list_defs(html_schema: dict, max_depth: int = None) -> list[tuple[str, str]]:
    """
    Extract actionable element names and descriptions from a schema's $defs section.

    Each $def represents a discrete user action (click, type, toggle).
    Properties within $defs are informational (data visible to agent) and are NOT
    extracted as separate elements.

    Elements with dot notation in their $def name (e.g., "itemCustomizationModal.addToCartButton")
    are treated as distinct actions within a modal context.

    Args:
        html_schema: The HTML schema dictionary
        max_depth: Deprecated, kept for backward compatibility. No longer used since
                   we only extract top-level $defs, not nested properties.

    Returns:
        Tuple of (elements_list, modals_dict) where:
        - elements_list: List of (element_name, description) tuples (excluding modal elements)
        - modals_dict: Dict mapping modal base names to their (element_name, description) tuples
    """
    from collections import defaultdict

    results = []
    modals = defaultdict(list)
    defs = html_schema.get("$defs", {})

    for def_name, def_value in defs.items():
        # Extract description if available
        description = (
            def_value.get("description", "") if isinstance(def_value, dict) else ""
        )
        element_tuple = (def_name, description)

        # Check if this is a modal-related element (contains "modal" in the name)
        if "modal" in def_name.lower():
            # Extract the modal base name (everything before the first dot, or the whole name)
            modal_base = def_name.split(".")[0]
            modals[modal_base].append(element_tuple)
        else:
            results.append(element_tuple)

    return results, modals


def format_elements(elements_tuple):
    """
    Format the result from list_defs, keeping main content and modal content separate.
    Includes descriptions for each element to help with disambiguation.

    Args:
        elements_tuple: Tuple of (results, modals) from list_defs where each element
                       is a (name, description) tuple

    Returns:
        Formatted string with main content and modal content sections
    """
    results, modals = elements_tuple

    output = []

    output.append("<main_content>")
    for i, element in enumerate(results, 1):
        if isinstance(element, tuple):
            name, description = element
            if description:
                output.append(f"{i:4d}. {name} - {description}")
            else:
                output.append(f"{i:4d}. {name}")
        else:
            # Backward compatibility for plain strings
            output.append(f"{i:4d}. {element}")
    output.append("</main_content>")

    # Format modal content
    if modals:
        output.append("<modals>")
        for modal_name, modal_elements in modals.items():
            output.append(f"\nModal: {modal_name}")
            for i, element in enumerate(modal_elements, 1):
                if isinstance(element, tuple):
                    name, description = element
                    if description:
                        output.append(f"{i:4d}. {name} - {description}")
                    else:
                        output.append(f"{i:4d}. {name}")
                else:
                    output.append(f"{i:4d}. {element}")
        output.append("</modals>")
    return "\n".join(output)
