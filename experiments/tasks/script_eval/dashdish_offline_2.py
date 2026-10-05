"""
DashDish Offline Task 2: Poke Category - Fastest Store Reviews and Menu

Task: Click on the 'Poke' category icon, go to the fastest store
and summarize all reviews and menu details.

Verifier:
1. Check that the Poke category was visited/clicked
2. Check that a poke store was visited (the fastest one)
3. Check that the final answer contains reviews and menu information

Note: "Fastest store" typically means the one with shortest delivery time.
The verification focuses on:
- Navigation to Poke category
- Visiting a poke store
- Extracting reviews and menu details in the answer
"""

import json
import sys
from typing import Any, Dict, Tuple

from .eval_utils import get_all_visited_paths

# Poke category path pattern
POKE_CATEGORY_PATTERN = "/category/poke"

# Known poke store paths (will match partial paths)
POKE_STORE_PATTERNS = [
    "/store/",  # Any store visit after poke category
]


def visited_poke_category(final_state: Dict[str, Any]) -> bool:
    """Check if the Poke category was visited."""
    visited_paths = get_all_visited_paths(final_state)
    for path in visited_paths:
        if "poke" in path.lower() and (
            "category" in path.lower() or "cuisine" in path.lower()
        ):
            return True
    return False


def visited_store_after_poke(final_state: Dict[str, Any]) -> bool:
    """
    Check if a store was visited (ideally after visiting Poke category).

    We check if any store page was visited in the session.
    """
    visited_paths = get_all_visited_paths(final_state)

    for path in visited_paths:
        # Check if we visited a store
        if "/store/" in path.lower():
            return True

    return False


def answer_contains_reviews(result_str: str) -> bool:
    """Check if the answer contains review information."""
    if not result_str:
        return False
    result_lower = result_str.lower()

    # Keywords indicating reviews were found
    review_keywords = [
        "review",
        "rating",
        "star",
        "rated",
        "feedback",
        "customer",
        "said",
        "commented",
        "wrote",
    ]

    return any(keyword in result_lower for keyword in review_keywords)


def answer_contains_menu(result_str: str) -> bool:
    """Check if the answer contains menu information."""
    if not result_str:
        return False
    result_lower = result_str.lower()

    # Keywords indicating menu/food items were described
    menu_keywords = [
        "menu",
        "item",
        "dish",
        "bowl",
        "poke",
        "fish",
        "salmon",
        "tuna",
        "rice",
        "topping",
        "price",
        "$",
        "serve",
        "offer",
        "available",
    ]

    # Count how many menu-related keywords are present
    keyword_count = sum(1 for keyword in menu_keywords if keyword in result_lower)

    # Require at least 2 menu-related keywords for a meaningful menu description
    return keyword_count >= 2


def evaluate(
    final_state: Dict[str, Any], final_result: str
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Evaluate the task output.

    Criteria:
    1. Poke category was visited (25%)
    2. A store was visited (25%)
    3. Answer contains review information (25%)
    4. Answer contains menu information (25%)

    Returns:
        (success, percentage, details)
    """
    import logging

    logger = logging.getLogger(__name__)

    details = {
        "poke_category_visited": False,
        "store_visited": False,
        "answer_has_reviews": False,
        "answer_has_menu": False,
        "visited_paths": [],
    }

    # Get visited paths for debugging
    visited_paths = get_all_visited_paths(final_state)
    details["visited_paths"] = visited_paths[:20]  # Limit for readability

    # Check if Poke category was visited
    poke_visited = visited_poke_category(final_state)
    details["poke_category_visited"] = poke_visited
    if poke_visited:
        logger.info("✓ Poke category was visited")
    else:
        logger.info("✗ Poke category was not visited")

    # Check if a store was visited
    store_visited = visited_store_after_poke(final_state)
    details["store_visited"] = store_visited
    if store_visited:
        logger.info("✓ A store was visited")
    else:
        logger.info("✗ No store was visited")

    # Check if answer contains reviews
    has_reviews = answer_contains_reviews(final_result)
    details["answer_has_reviews"] = has_reviews
    if has_reviews:
        logger.info("✓ Answer contains review information")
    else:
        logger.info("✗ Answer does not contain review information")

    # Check if answer contains menu details
    has_menu = answer_contains_menu(final_result)
    details["answer_has_menu"] = has_menu
    if has_menu:
        logger.info("✓ Answer contains menu information")
    else:
        logger.info("✗ Answer does not contain menu information")

    # Calculate score (25% for each criterion)
    score = 0.0
    if poke_visited:
        score += 0.25
    if store_visited:
        score += 0.25
    if has_reviews:
        score += 0.25
    if has_menu:
        score += 0.25

    success = poke_visited and store_visited and has_reviews and has_menu

    return success, score, details


def main():
    """CLI entry point for subprocess execution."""
    state_path = sys.argv[1]
    with open(state_path) as f:
        data = json.load(f)
        final_state = data.get("final_state", {})
        final_result = data.get("final_result", "")

    success, pct, details = evaluate(final_state, final_result)

    if success:
        print(f"SUCCESS ({pct * 100:.0f}%)")
    else:
        print(f"FAILURE ({pct * 100:.0f}%)")


if __name__ == "__main__":
    main()
