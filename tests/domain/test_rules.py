from support.facts import make_facts

from aeca.domain.labels import Label
from aeca.domain.links import Link
from aeca.domain.rules import RuleVerdict, rule_verdict

MISMATCHED = Link(text="bank.com", target="https://evil.example/", mismatched=True)
MATCHING = Link(text="example.com", target="https://example.com/", mismatched=False)


def test_dmarc_fail_and_a_mismatched_link_is_spam() -> None:
    facts = make_facts(dmarc="fail", links=(MATCHING, MISMATCHED))

    assert rule_verdict(facts) == RuleVerdict(Label.SPAM, "dmarc_fail_and_link_mismatch")


def test_dmarc_fail_alone_decides_nothing() -> None:
    assert rule_verdict(make_facts(dmarc="fail", links=(MATCHING,))) is None


def test_a_mismatched_link_alone_decides_nothing() -> None:
    assert rule_verdict(make_facts(dmarc="pass", links=(MISMATCHED,))) is None


def test_a_missing_dmarc_result_decides_nothing() -> None:
    assert rule_verdict(make_facts(dmarc=None, links=(MISMATCHED,))) is None
