"""
DashDish Offline Task 1: Order Chicken Biryani from Mashaallah

Task: Can you find out how much a medium 'Chicken Biryani' from
'Mashaallah Halal Food Pakistani Food' costs? And while you're at it,
add one to the cart, buy it, and let me know the total price.

Verifier:
1. Check that Mashaallah Halal Food store was visited
2. Check that a Chicken Biryani was ordered (placed, not just in cart)
3. Check that the answer contains price information

Expected price for medium Chicken Biryani: $15.99
With fees, the total should be around $20-25 depending on delivery/service fees.
"""

import json
import sys
from typing import Any, Dict, Tuple

from .constants import StorePaths
from .eval_utils import check_path, extract_orders

# Keywords to identify Chicken Biryani
BIRYANI_KEYWORDS = ["biryani", "chicken biryani"]

# Expected prices (approximate - used for answer validation)
ITEM_PRICE = "15.99"
# Price variations that might appear in the answer
PRICE_PATTERNS = ["15.99", "15,99", "$15.99", "$16"]


def check_answer_has_price(result_str: str) -> bool:
    """Check that the answer mentions price information."""
    if not result_str:
        return False
    result_lower = result_str.lower()
    # Check for any price pattern
    for pattern in PRICE_PATTERNS:
        if pattern.lower() in result_lower:
            return True
    # Also check for generic price indicators with numbers
    return "$" in result_str or "price" in result_lower or "cost" in result_lower


def contains_biryani(text: str) -> bool:
    """Check if text contains biryani keywords (case-insensitive)."""
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in BIRYANI_KEYWORDS)


def order_has_biryani(order: Dict[str, Any]) -> bool:
    """Check if an order contains Chicken Biryani."""
    items = order.get("cartItems", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        desc = item.get("description", "")
        if contains_biryani(name) or contains_biryani(desc):
            return True
    return False


def evaluate(
    final_state: Dict[str, Any], final_result: str
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Evaluate the task output.

    Criteria:
    1. Mashaallah store was visited (33%)
    2. Chicken Biryani order was placed (34%)
    3. Answer contains price information (33%)

    Returns:
        (success, percentage, details)
    """
    import logging

    logger = logging.getLogger(__name__)

    details = {
        "store_visited": False,
        "order_placed": False,
        "answer_has_price": False,
        "expected_store": StorePaths.MASHAALLAH,
        "orders_found": 0,
        "biryani_orders_found": 0,
    }

    # Check if Mashaallah store was visited
    store_visited = check_path(final_state, [StorePaths.MASHAALLAH])
    details["store_visited"] = store_visited
    if store_visited:
        logger.info("✓ Mashaallah Halal Food store was visited")
    else:
        logger.info("✗ Mashaallah Halal Food store was not visited")

    # Check if order was placed with Chicken Biryani
    orders = extract_orders(final_state)
    details["orders_found"] = len(orders)

    biryani_order_count = 0
    for order in orders:
        if order_has_biryani(order):
            biryani_order_count += 1

    details["biryani_orders_found"] = biryani_order_count
    details["order_placed"] = biryani_order_count >= 1

    if details["order_placed"]:
        logger.info(f"✓ Found {biryani_order_count} order(s) with Chicken Biryani")
    else:
        logger.info("✗ No order with Chicken Biryani found")

    # Check if answer contains price information
    answer_has_price = check_answer_has_price(final_result)
    details["answer_has_price"] = answer_has_price
    if answer_has_price:
        logger.info("✓ Answer contains price information")
    else:
        logger.info("✗ Answer does not contain price information")

    # Calculate score
    score = 0.0
    if store_visited:
        score += 1 / 3
    if details["order_placed"]:
        score += 1 / 3
    if answer_has_price:
        score += 1 / 3

    success = store_visited and details["order_placed"] and answer_has_price

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
