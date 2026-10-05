"""
GoMail Offline Task 1: Create Label and Send Thank You Email

Task: Create a new label called "Thank You" and send a quick thank you
note to bob@example.com.

Verifier:
1. Check that a "Thank You" label was created
2. Check that an email was sent to bob@example.com

Scoring: 50% for label creation, 50% for email sent
"""

import json
import sys
from typing import Any, Dict, List, Set, Tuple

# Expected label (case-insensitive matching)
EXPECTED_LABEL = "thank you"

# Expected recipient
EXPECTED_RECIPIENT = "bob@example.com"


def normalize_str(s: Any) -> str:
    """Normalize string for comparison (strip whitespace, lowercase)."""
    return (s or "").strip().lower()


def find_created_labels(data: Dict[str, Any]) -> Set[str]:
    """
    Find all labels that were created.

    Searches in multiple possible locations:
    - initialfinaldiff.added.settings.customLabels (primary location in GoMail)
    - initialfinaldiff.added.email.labels
    - differences.labels.added
    - Action history for settings/addCustomLabel
    """
    created_labels: Set[str] = set()

    # Search initialfinaldiff
    initdiff = data.get("initialfinaldiff")
    if isinstance(initdiff, dict):
        added = initdiff.get("added", {})

        # Check settings.customLabels (primary location for GoMail labels)
        settings_labels = added.get("settings", {}).get("customLabels")
        if isinstance(settings_labels, dict):
            for key, val in settings_labels.items():
                if isinstance(val, dict):
                    name = val.get("name") or val.get("label") or key
                    created_labels.add(normalize_str(name))
                elif isinstance(val, str):
                    created_labels.add(normalize_str(val))
        elif isinstance(settings_labels, list):
            for item in settings_labels:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label")
                    if name:
                        created_labels.add(normalize_str(name))
                elif isinstance(item, str):
                    created_labels.add(normalize_str(item))

        # Check email.labels
        email_labels = added.get("email", {}).get("labels")
        if isinstance(email_labels, dict):
            for key, val in email_labels.items():
                if isinstance(val, dict):
                    name = val.get("name") or val.get("label") or key
                    created_labels.add(normalize_str(name))
                elif isinstance(val, str):
                    created_labels.add(normalize_str(val))
                else:
                    created_labels.add(normalize_str(key))
        elif isinstance(email_labels, list):
            for item in email_labels:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label")
                    if name:
                        created_labels.add(normalize_str(name))
                elif isinstance(item, str):
                    created_labels.add(normalize_str(item))

        # Check direct labels under added
        direct_labels = added.get("labels")
        if isinstance(direct_labels, dict):
            for key, val in direct_labels.items():
                if isinstance(val, dict):
                    name = val.get("name") or val.get("label") or key
                    created_labels.add(normalize_str(name))
                elif isinstance(val, str):
                    created_labels.add(normalize_str(val))
                else:
                    created_labels.add(normalize_str(key))
        elif isinstance(direct_labels, list):
            for item in direct_labels:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label")
                    if name:
                        created_labels.add(normalize_str(name))
                elif isinstance(item, str):
                    created_labels.add(normalize_str(item))

    # Search differences.labels
    differences = data.get("differences", {})
    diff_labels = differences.get("labels", {})

    if isinstance(diff_labels, dict):
        added_labels = diff_labels.get("added")
        if isinstance(added_labels, dict):
            for key, val in added_labels.items():
                if isinstance(val, dict):
                    name = val.get("name") or val.get("label") or key
                    created_labels.add(normalize_str(name))
                elif isinstance(val, str):
                    created_labels.add(normalize_str(val))
                else:
                    created_labels.add(normalize_str(key))
        elif isinstance(added_labels, list):
            for item in added_labels:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label")
                    if name:
                        created_labels.add(normalize_str(name))
                elif isinstance(item, str):
                    created_labels.add(normalize_str(item))

    # Search action history for label creation actions
    action_history = data.get("actionhistory", [])
    for action in action_history:
        if not isinstance(action, dict):
            continue
        action_type = action.get("type", "")
        # Look for settings/addCustomLabel actions (GoMail's label creation)
        if action_type == "settings/addCustomLabel":
            payload = action.get("payload", {})
            if isinstance(payload, dict):
                label_obj = payload.get("label", {})
                if isinstance(label_obj, dict):
                    name = label_obj.get("label") or label_obj.get("name")
                    if name:
                        created_labels.add(normalize_str(name))
        # Also check generic label creation patterns
        elif "label" in action_type.lower() and (
            "create" in action_type.lower() or "add" in action_type.lower()
        ):
            payload = action.get("payload", {})
            if isinstance(payload, dict):
                name = (
                    payload.get("name")
                    or payload.get("label")
                    or payload.get("labelName")
                )
                if name:
                    created_labels.add(normalize_str(name))

    return created_labels


def get_added_emails(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all added emails from the state.

    Checks both:
    - differences.emails.added (list)
    - initialfinaldiff.added.email.emails (dict)
    """
    added_emails: List[Dict[str, Any]] = []

    # Check differences.emails.added
    differences = data.get("differences") or {}
    emails_diff = differences.get("emails") or {}
    added = emails_diff.get("added") or []

    if isinstance(added, list):
        for msg in added:
            if isinstance(msg, dict):
                added_emails.append(msg)

    # Also check initialfinaldiff.added.email.emails
    init = data.get("initialfinaldiff")
    if isinstance(init, dict):
        added_dict = init.get("added", {}).get("email", {}).get("emails")
        if isinstance(added_dict, dict):
            for v in added_dict.values():
                if isinstance(v, dict):
                    added_emails.append(v)

    return added_emails


def check_email_sent_to_recipient(
    data: Dict[str, Any], expected_recipient: str
) -> bool:
    """Check if an email was sent to the expected recipient."""
    added_emails = get_added_emails(data)

    for email in added_emails:
        to_list = email.get("to") or []
        if isinstance(to_list, str):
            to_list = [to_list]

        for recipient in to_list:
            if normalize_str(recipient) == expected_recipient.lower():
                return True

    return False


def evaluate(
    final_state: Dict[str, Any], final_result: str
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Evaluate the task output.

    Criteria:
    1. "Thank You" label was created (50%)
    2. Email was sent to bob@example.com (50%)

    Returns:
        (success, percentage, details)
    """
    import logging

    logger = logging.getLogger(__name__)

    details = {
        "label_created": False,
        "email_sent": False,
        "expected_label": EXPECTED_LABEL,
        "expected_recipient": EXPECTED_RECIPIENT,
        "created_labels": [],
        "emails_found": 0,
    }

    # Check if "Thank You" label was created
    created_labels = find_created_labels(final_state)
    details["created_labels"] = list(created_labels)

    label_created = EXPECTED_LABEL in created_labels
    details["label_created"] = label_created

    if label_created:
        logger.info("✓ 'Thank You' label was created")
    else:
        logger.info(f"✗ 'Thank You' label was not found. Found: {created_labels}")

    # Check if email was sent to bob@example.com
    email_sent = check_email_sent_to_recipient(final_state, EXPECTED_RECIPIENT)
    details["email_sent"] = email_sent
    details["emails_found"] = len(get_added_emails(final_state))

    if email_sent:
        logger.info(f"✓ Email was sent to {EXPECTED_RECIPIENT}")
    else:
        logger.info(f"✗ No email found sent to {EXPECTED_RECIPIENT}")

    # Calculate score (50% for each criterion)
    score = 0.0
    if label_created:
        score += 0.5
    if email_sent:
        score += 0.5

    success = label_created and email_sent

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
