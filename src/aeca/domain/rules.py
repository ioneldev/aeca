"""The rules that decide an email's label without asking the model."""

from dataclasses import dataclass

from aeca.domain.facts import EmailFacts
from aeca.domain.labels import Label

DMARC_FAIL_AND_LINK_MISMATCH = "dmarc_fail_and_link_mismatch"


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    label: Label
    rule: str


def rule_verdict(facts: EmailFacts) -> RuleVerdict | None:
    if _failed_dmarc_and_misleading_link(facts):
        return RuleVerdict(Label.SPAM, DMARC_FAIL_AND_LINK_MISMATCH)
    return None


def _failed_dmarc_and_misleading_link(facts: EmailFacts) -> bool:
    return facts.auth.dmarc == "fail" and any(link.mismatched for link in facts.links)
