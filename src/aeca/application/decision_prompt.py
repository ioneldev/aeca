"""Builds what the model reads: a stable system prompt, and one first message per email."""

import json
from datetime import datetime
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo

from aeca.domain.facts import EmailFacts
from aeca.domain.labels import LABEL_RECORDS, URGENCY_DEFINITION, LabelRecord

MAX_LINKS_SHOWN = 20
FENCE_OPEN = "<<<EMAIL"
FENCE_CLOSE = "EMAIL>>>"
# A zero-width space inside the closing marker keeps its look and loses its meaning.
DEFUSED_FENCE_CLOSE = "EMAIL>>​>"


# Read once, so the prompt that caches is the same string on every call by construction.
@cache
def system_prompt() -> str:
    template = files("aeca.application").joinpath("prompts", "decision.md").read_text("utf-8")
    return template.replace("{{LABELS}}", _label_section()).replace(
        "{{URGENCY}}", URGENCY_DEFINITION
    )


def first_user_message(facts: EmailFacts, *, now: datetime, zone: ZoneInfo) -> str:
    local_now = now.astimezone(zone).isoformat()
    return (
        f"Now: {local_now}\n"
        f"Time zone: {zone.key}\n"
        f"FACTS\n{_defused(_facts_json(facts, zone))}\n"
        f"{FENCE_OPEN}\n{_defused(facts.text)}\n{FENCE_CLOSE}"
    )


def _defused(text: str) -> str:
    return text.replace(FENCE_CLOSE, DEFUSED_FENCE_CLOSE)


def _facts_json(facts: EmailFacts, zone: ZoneInfo) -> str:
    document = {
        "sender": facts.sender,
        "sender_name": facts.sender_name,
        "subject": facts.subject,
        "reply_to_differs": facts.reply_to_differs,
        "internal": facts.internal,
        "automated": facts.automated,
        "bulk": facts.bulk,
        "priority_high": facts.priority_high,
        "auth": {"spf": facts.auth.spf, "dkim": facts.auth.dkim, "dmarc": facts.auth.dmarc},
        "links": [
            {"text": link.text, "target": link.target, "mismatched": link.mismatched}
            for link in facts.links[:MAX_LINKS_SHOWN]
        ],
        "attachments": [
            {"name": attachment.name, "mime_type": attachment.mime_type}
            for attachment in facts.attachments
        ],
        "label_ids": sorted(facts.label_ids),
        "received_at": facts.received_at.astimezone(zone).isoformat(),
    }
    return json.dumps(document, sort_keys=True, ensure_ascii=False)


def _label_section() -> str:
    return "\n\n".join(_label_entry(record) for record in LABEL_RECORDS.values())


def _label_entry(record: LabelRecord) -> str:
    replies = ", ".join(sorted(reply.value for reply in record.allowed_replies)) or "none"
    examples = "\n".join(f"  - {example}" for example in record.examples)
    return (
        f"### {record.label.value}\n{record.description}\n"
        f"Allowed replies: {replies}\nExamples:\n{examples}"
    )
