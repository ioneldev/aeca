"""Builds corpus emails for tests, so no test depends on what the real corpus holds."""

from typing import Any

from aeca.tools.corpus import CorpusEmail

DEFAULT_EXPECTED: dict[str, Any] = {
    "label": "INFO_ONLY",
    "urgent": False,
    "actions": ["apply_label"],
    "approval": False,
    "reply": "none",
    "calendar": None,
}


def make_corpus_email(**overrides: Any) -> CorpusEmail:
    fields: dict[str, Any] = {
        "id": "case",
        "sender_role": "external",
        "from_name": "Sam Tester",
        "subject": "A subject",
        "body": "A body",
        "html": False,
        "headers": {},
        "deliverable": True,
        "expected": DEFAULT_EXPECTED,
    }
    return CorpusEmail.model_validate({**fields, **overrides})
