"""Ids derived from the source Gmail message, never from a counter, so a fresh state store can
never reuse one."""

import base64
import hashlib

EVENT_ID_LENGTH = 26


def action_key(source_message_id: str, step: str) -> str:
    return "k-" + _sha256_hex(f"{source_message_id}:{step}")[:16]


def approval_id(source_message_id: str, attempt: int) -> str:
    return "a-" + _sha256_hex(f"approval:{source_message_id}:{attempt}")[:12]


def event_id(source_message_id: str, attempt: int) -> str:
    digest = hashlib.sha256(f"event:{source_message_id}:{attempt}".encode()).digest()
    encoded = base64.b32hexencode(digest).decode("ascii").rstrip("=")
    return "aeca" + encoded[:EVENT_ID_LENGTH].lower()


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
