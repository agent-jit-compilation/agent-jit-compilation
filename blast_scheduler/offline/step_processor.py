from typing import Dict

import openai

from blast_scheduler.offline.schema import format_elements, list_defs
from blast_scheduler.views import Step, TraceStep

_SYSTEM_PROMPT_PROCESS_STEP = """
You are an expert at analyzing the actions of an AI agent. 
Given an agent step, your task is to fill out the following tuple (id: int, goal_element: str, success: bool, is_modal: bool, modal_name: str)

CRITICAL CONCEPT - Elements = User Actions:
Each element in the valid_elements list represents a DISCRETE USER ACTION (click, type, toggle).
Elements are NOT data properties. For example:
- "restaurantCard" = the action of clicking a restaurant card (which navigates to menu)
- "addToCartButton" = the action of clicking to add an item to cart
- "searchInput" = the action of typing in the search field
These are only examples and you should use actual element names from the valid_elements list.

DO NOT use property-like names (e.g., "restaurantCard.restaurantName" or "restaurantCard.destinationUrl").
These are informational properties visible when interacting with an element, NOT separate actions.

Explanation:
In each step, the agent performs an action by interacting with a UI element.
1. The next_goal describing what the agent wants to do
2. The thinking explaining the agent's reasoning

You should carefully read the agent's next_goal and thinking to determine which UI element the agent's *ultimate goal* is to interact with.

CRITICAL DISTINCTION - Intermediate vs Ultimate Goal:
Many agent steps are EXPLORATORY (e.g., scrolling, extracting data, clicking to reveal elements). These are intermediate steps taken to REACH the ultimate goal element.

Examples:
1. If the agent says "scroll to find the submit button" and takes a scroll action:
   - WRONG: goal_element = "scrollable container" 
   - CORRECT: goal_element = "submitButton" (the ultimate target)

2. If the agent says "click on the restaurant card to go to its menu page":
   - WRONG: goal_element = "restaurantCard.destinationUrl" (destinationUrl is just data, not an action)
   - CORRECT: goal_element = "restaurantCard" (the action is clicking the card)

3. If the agent says "extract structured data to check for add-to-cart buttons" and takes an extract action:
   - CORRECT: goal_element = "addToCartButton" or similar (what they're looking for)

4. If the agent says "click the menu icon to open navigation" and takes a click action:
   - CORRECT: goal_element = "menuIcon" (this IS the ultimate goal - opening navigation)

IMPORTANT GUIDELINES:
- The goal_element MUST be from the list of valid elements below
- Elements represent ACTIONS, not data properties - always use the parent element name
- If the goal_element is from a modal, set is_modal to True and modal_name to the name of the modal
- Look for keywords in next_goal and thinking that indicate the TRUE target
- If the action is exploratory (scroll, extract, wait), identify what element they're trying to FIND or REACH
- If the action directly accomplishes a goal (click button, fill form), use that element

Your output should be a list of JSON objects with the following fields:
{{
    "id": the step number,
    "goal_element": the element the agent's ultimate goal is to interact with (not intermediate exploratory elements)
    "success": whether the agent's goal was accomplished
    "is_modal": whether the goal_element is from a modal
    "modal_name": the name of the modal if is_modal is True. Empty string if is_modal is False
}}
"""

_USER_PROMPT_PROCESS_STEP = """
<valid_elements>
{valid_elements}
</valid_elements>

<trace>
{trace}
</trace>
"""


def process_step(
    client: openai.OpenAI,
    step: TraceStep,
    urls_to_schemas: Dict[str, dict],
    max_depth: int,
    max_retries: int = 10,
    model: str = "gpt-4.1",
) -> Step:
    """
    Process a single trace step and map it to a schema element.

    Args:
        client: OpenAI client
        step: Current trace step
        next_step: Next trace step (for evaluation), optional
        urls_to_schemas: Mapping from URLs to their schemas
        max_retries: Maximum number of retries for LLM calls
        model: Model to use for processing (default: "gpt-4.1")

    Returns:
        Step object with mapped element information
    """
    html_schema = urls_to_schemas.get(step.starting_url)

    # Fall back to main page schema for empty URLs (initial navigation steps)
    if not html_schema and not step.starting_url:
        # Try to find a main page schema (URL pattern: "https://domain.com/ - SiteName")
        for url, schema in urls_to_schemas.items():
            # Main page URLs end with "/ - " after the domain (no path segments)
            # e.g., "https://real-omnizon.vercel.app/ - Omnizon"
            if "/ - " in url:
                path_part = url.split("/ - ")[0]
                # Count path segments after domain - main page has none
                parts_after_domain = path_part.split("://")[-1].split("/")
                if len(parts_after_domain) <= 1 or (
                    len(parts_after_domain) == 2 and parts_after_domain[1] == ""
                ):
                    html_schema = schema
                    break

    if not html_schema:
        raise ValueError(f"No schema found for URL: {step.starting_url}")

    trace = {
        "id": step.step,
        "next_goal": step.next_goal,
        "thinking": step.thinking,
    }

    # Get elements with descriptions and format them
    elements_tuple = list_defs(html_schema, max_depth=max_depth)
    valid_elements_formatted = format_elements(elements_tuple)

    # Extract just element names for validation (handle both tuple and string formats)
    results, modals = elements_tuple
    valid_element_names = []
    for item in results:
        if isinstance(item, tuple):
            valid_element_names.append(
                item[0]
            )  # element_name from (element_name, description)
        else:
            valid_element_names.append(item)  # backward compatibility

    # Add modal elements
    for modal_elements in modals.values():
        for item in modal_elements:
            if isinstance(item, tuple):
                valid_element_names.append(item[0])
            else:
                valid_element_names.append(item)

    valid_elements = valid_elements_formatted

    # Build conversation history for retries
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT_PROCESS_STEP,
        },
        {
            "role": "user",
            "content": _USER_PROMPT_PROCESS_STEP.format(
                trace=trace,
                valid_elements=valid_elements,
            ),
        },
    ]

    for i in range(max_retries):
        resp = client.responses.parse(
            model=model,
            input=messages,
            text_format=Step,
        )

        if resp.output_parsed.goal_element in valid_element_names:
            return resp.output_parsed

        invalid_element = resp.output_parsed.goal_element
        print(
            f"Warning: Invalid element '{invalid_element}' "
            f"(retry {i + 1}/{max_retries})"
        )

        # Add the invalid response and correction feedback for the next retry
        messages.append(
            {
                "role": "assistant",
                "content": resp.output_text,
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Invalid response: '{invalid_element}' is not in the valid_elements list. "
                    f"You MUST choose an element from the valid_elements list exactly as written. "
                    f"Please try again."
                ),
            }
        )

    # If all retries fail, return None
    print(
        f"Warning: Failed to get valid element after {max_retries} retries. "
        f"Returning None."
    )
    return None
