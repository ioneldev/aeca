import pytest
from support.emails import make_raw
from support.facts import ACCOUNT_ADDRESS, make_facts

from aeca.domain.facts import FactSettings, extract_facts
from aeca.domain.labels import Label
from aeca.domain.replies import ReplyContext, check_urgent_draft, reply_block_reason


def context(
    label: Label = Label.TASK, *, thread_has_unapproved_reply: bool = False
) -> ReplyContext:
    return ReplyContext(
        label=label,
        account_address=ACCOUNT_ADDRESS,
        thread_has_unapproved_reply=thread_has_unapproved_reply,
    )


def test_a_plain_sender_may_be_replied_to() -> None:
    assert reply_block_reason(make_facts(), context()) is None


@pytest.mark.parametrize(
    ("facts_overrides", "reply_context"),
    [
        ({}, context(Label.SPAM)),
        ({"sender": "AE.CA@googlemail.com"}, context()),
        ({"dmarc": "fail"}, context()),
        ({"automated": True}, context()),
        ({}, context(thread_has_unapproved_reply=True)),
    ],
    ids=["spam", "self", "dmarc-fail", "automated", "second-unapproved-in-thread"],
)
def test_no_reply_to_spam_self_dmarc_fail_automated_or_a_second_unapproved_in_the_thread(
    facts_overrides: dict[str, object], reply_context: ReplyContext
) -> None:
    reason = reply_block_reason(make_facts(**facts_overrides), reply_context)

    assert reason


def test_each_rule_gives_its_own_reason() -> None:
    reasons = {
        reply_block_reason(make_facts(), context(Label.SPAM)),
        reply_block_reason(make_facts(sender="aeca@gmail.com"), context()),
        reply_block_reason(make_facts(dmarc="fail"), context()),
        reply_block_reason(make_facts(automated=True), context()),
        reply_block_reason(make_facts(), context(thread_has_unapproved_reply=True)),
    }

    assert len(reasons) == 5


def test_a_reply_goes_to_from_never_reply_to() -> None:
    settings = FactSettings(ACCOUNT_ADDRESS, frozenset(), frozenset())
    raw = make_raw(sender="real@example.com", headers={"Reply-To": "phisher@evil.example"})
    facts = extract_facts(raw, settings)

    assert facts.reply_to_differs
    assert facts.sender == "real@example.com"
    assert reply_block_reason(facts, context()) is None


@pytest.mark.parametrize(
    "draft",
    [
        "See https://example.com for details",
        "See www.example.com for details",
        "Open evil.example.com/login now",
        "Go to example.com today",
        "Go to evil.example/login today",
        "Write to boss@example.com",
        "Open evil​.com now",
        "a" * 801,
    ],
)
def test_a_draft_with_a_link_an_address_or_too_many_characters_is_dropped(draft: str) -> None:
    assert check_urgent_draft(draft)


@pytest.mark.parametrize(
    "draft", ["On it, I will call you at 3 p.m.", "Thanks, e.g. tomorrow. Will do.", "a" * 800]
)
def test_an_ordinary_draft_is_kept(draft: str) -> None:
    assert check_urgent_draft(draft) is None
