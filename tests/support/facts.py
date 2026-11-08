"""Builds EmailFacts for tests that care about the decision, not about parsing."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from support.emails import make_raw

from aeca.domain.auth_results import AuthResults
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.links import Link

ACCOUNT_ADDRESS = "aeca@gmail.com"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_facts(
    *,
    subject: str = "Hello",
    text: str = "Hi there",
    internal: bool = False,
    dmarc: str | None = None,
    links: tuple[Link, ...] = (),
    **overrides: Any,
) -> EmailFacts:
    settings = FactSettings(
        account_address=ACCOUNT_ADDRESS, internal_senders=frozenset(), bulk_senders=frozenset()
    )
    base = extract_facts(make_raw(subject=subject, body=text), settings)
    return replace(
        base,
        internal=internal,
        auth=AuthResults(spf=None, dkim=None, dmarc=dmarc),
        links=links,
        **overrides,
    )
