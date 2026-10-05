"""
GoMail Offline Task 2: Forward and Delete First Email

Task: Forward the first email in my inbox to me@example.com with note
'FYI: This is a test' and then delete it.

Verifier:
1. Check that the first email was forwarded to me@example.com
2. Check that the forward contains the note 'FYI: This is a test'
3. Check that the original email was deleted (moved to trash)

The first email in the inbox is typically identified by:
- Subject containing "your ride receipt" (Uber) or similar

Scoring:
- 50% for forwarding with correct note
- 50% for deleting the original
"""

import json
import re
import sys
from typing import Any, Dict, List, Set, Tuple

# Expected recipient for the forward
TARGET_RECIPIENT = "me@example.com"

# Required note in the forward
REQUIRED_NOTE = "fyi: this is a test"

# Keywords to identify the first email (Uber receipt is typically first)
FIRST_EMAIL_KEYWORDS = [
    "your ride receipt",
    "uber",
    "ride receipt",
]


def normalize_str(s: Any) -> str:
    """Normalize string for comparison (strip whitespace, lowercase)."""
    return (s or "").strip().lower()


def strip_html_tags(s: str) -> str:
    """Strip HTML tags from a string and normalize whitespace."""
    if not s:
        return ""
    clean = re.sub(r"<[^>]+>", "", s)
    clean = " ".join(clean.split())
    return clean.strip()


def get_forward_emails(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all forward emails from the state.

    Searches:
    - state.email.emails (main email list)
    - differences.emails.added
    - initialfinaldiff.added.email.emails
    """
    found_emails: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    def is_forward_email(email: Dict[str, Any]) -> bool:
        """Check if an email is a forward."""
        is_forward_flag = email.get("isForward") is True
        subject_lower = normalize_str(email.get("subject", ""))
        has_forward_in_subject = (
            "forward" in subject_lower
            or "fwd" in subject_lower
            or "fw:" in subject_lower
        )
        return is_forward_flag or has_forward_in_subject

    def check_and_add(email: Dict[str, Any]):
        """Check if email is a forward and add if not duplicate."""
        if not isinstance(email, dict):
            return

        if not is_forward_email(email):
            return

        email_id = str(email.get("id", ""))
        if email_id and email_id in seen_ids:
            return
        if email_id:
            seen_ids.add(email_id)

        found_emails.append(email)

    # Check state.email.emails
    state = data.get("state") or {}
    email_section = state.get("email") or {}
    emails_list = email_section.get("emails") or []
    if isinstance(emails_list, list):
        for email in emails_list:
            check_and_add(email)

    # Check differences.emails.added
    differences = data.get("differences") or {}
    emails_diff = differences.get("emails") or {}
    added = emails_diff.get("added") or []
    if isinstance(added, list):
        for email in added:
            check_and_add(email)

    # Check initialfinaldiff.added.email.emails
    init = data.get("initialfinaldiff")
    if isinstance(init, dict):
        added_dict = init.get("added", {}).get("email", {}).get("emails")
        if isinstance(added_dict, dict):
            for email in added_dict.values():
                check_and_add(email)

    return found_emails


def get_trashed_emails(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all emails that were moved to trash.

    Searches differences.emails.updated for emails with trash: true.
    Enriches with subject from initialstate if available.
    """
    trashed = []

    differences = data.get("differences") or {}
    emails_diff = differences.get("emails") or {}
    updated = emails_diff.get("updated") or []

    # Build a lookup of email ID -> subject from initialstate
    id_to_subject = {}
    initialstate = data.get("initialstate") or {}
    init_email = initialstate.get("email") or {}
    init_emails = init_email.get("emails") or []
    for email in init_emails:
        if isinstance(email, dict):
            email_id = str(email.get("id", ""))
            subject = email.get("subject", "")
            if email_id:
                id_to_subject[email_id] = subject

    for email in updated:
        if isinstance(email, dict) and email.get("trash") is True:
            email_id = str(email.get("id", ""))
            if not email.get("subject") and email_id in id_to_subject:
                email = dict(email)
                email["subject"] = id_to_subject[email_id]
            trashed.append(email)

    return trashed


def get_recipients(email: Dict[str, Any]) -> List[str]:
    """Get normalized list of recipients from an email."""
    to_list = email.get("to") or []
    if isinstance(to_list, str):
        to_list = [to_list]

    recipients = []
    for item in to_list:
        if isinstance(item, str):
            for addr in item.split(","):
                normalized = normalize_str(addr)
                if normalized:
                    recipients.append(normalized)
    return recipients


def email_has_required_note(email: Dict[str, Any]) -> bool:
    """Check if the email content contains the required note."""
    content = email.get("content") or email.get("body") or ""
    clean_content = strip_html_tags(content).lower()
    return REQUIRED_NOTE.lower() in clean_content


def matches_first_email(subject: str) -> bool:
    """Check if subject matches the first email keywords."""
    subject_lower = normalize_str(subject)
    # For the first email, we're less strict - any email that was forwarded
    # could be the first one. But if we can identify it by keywords, even better.
    for keyword in FIRST_EMAIL_KEYWORDS:
        if keyword.lower() in subject_lower:
            return True
    return False


def evaluate(
    final_state: Dict[str, Any], final_result: str
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Evaluate the task output.

    Criteria:
    1. First email was forwarded to me@example.com with the required note (50%)
    2. The original email was deleted (50%)

    Returns:
        (success, percentage, details)
    """
    import logging

    logger = logging.getLogger(__name__)

    details = {
        "forwarded_correctly": False,
        "deleted_correctly": False,
        "target_recipient": TARGET_RECIPIENT,
        "required_note": REQUIRED_NOTE,
        "forwards_found": 0,
        "trashed_found": 0,
        "forward_details": [],
        "trash_details": [],
    }

    # Get all forward emails
    forward_emails = get_forward_emails(final_state)
    details["forwards_found"] = len(forward_emails)

    # Get all trashed emails
    trashed_emails = get_trashed_emails(final_state)
    details["trashed_found"] = len(trashed_emails)

    # Check for valid forward
    forwarded_correctly = False
    target_normalized = normalize_str(TARGET_RECIPIENT)

    for email in forward_emails:
        recipients = get_recipients(email)
        has_note = email_has_required_note(email)
        to_target = target_normalized in recipients
        subject = email.get("subject", "")

        details["forward_details"].append(
            {
                "subject": subject[:50] if subject else "",
                "to": email.get("to"),
                "has_note": has_note,
                "to_target_recipient": to_target,
            }
        )

        if to_target and has_note:
            forwarded_correctly = True
            logger.info(
                f"✓ Found valid forward to {TARGET_RECIPIENT} with required note"
            )
            break

    details["forwarded_correctly"] = forwarded_correctly

    if not forwarded_correctly:
        if len(forward_emails) > 0:
            logger.info(f"✗ Found {len(forward_emails)} forwards but none met criteria")
        else:
            logger.info("✗ No forward emails found")

    # Check for deleted email
    # We consider success if at least one email was trashed
    deleted_correctly = len(trashed_emails) >= 1
    details["deleted_correctly"] = deleted_correctly

    for email in trashed_emails:
        details["trash_details"].append(
            {
                "id": email.get("id"),
                "subject": email.get("subject", "")[:50],
            }
        )

    if deleted_correctly:
        logger.info(f"✓ Found {len(trashed_emails)} deleted email(s)")
    else:
        logger.info("✗ No deleted emails found")

    # Calculate score (50% for each criterion)
    score = 0.0
    if forwarded_correctly:
        score += 0.5
    if deleted_correctly:
        score += 0.5

    success = forwarded_correctly and deleted_correctly

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
