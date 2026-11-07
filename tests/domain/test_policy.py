from dataclasses import fields
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from support.emails import make_raw
from support.facts import ACCOUNT_ADDRESS, make_facts

from aeca.domain.actions import Action, Verb
from aeca.domain.decision import DecidedBy, Decision, Meeting
from aeca.domain.facts import EmailFacts, FactSettings, extract_facts
from aeca.domain.labels import Label, ReplyKind
from aeca.domain.meeting_outcome import (
    AlreadyOnCalendar,
    MeetingOutcome,
    NoSlotFree,
    SlotFree,
)
from aeca.domain.policy import (
    POLICY_STEPS,
    PREDICATES,
    URGENT_STEPS,
    ActionPlan,
    PolicyContext,
    PolicySettings,
    plan_actions,
)
from aeca.domain.replies import (
    AUTOMATED_SENDER,
    DMARC_FAILED,
)
from aeca.domain.time_range import Interval

BUCHAREST = ZoneInfo("Europe/Bucharest")


def slot_at(day: int, hour: int) -> Interval:
    return Interval(
        datetime(2026, 11, day, hour, 0, tzinfo=BUCHAREST),
        datetime(2026, 11, day, hour, 30, tzinfo=BUCHAREST),
    )


SLOT = slot_at(10, 15)
THREE_SLOTS = (slot_at(10, 9), slot_at(10, 10), slot_at(11, 9))


def decision(
    label: Label,
    *,
    reply: ReplyKind = ReplyKind.NONE,
    finance: bool = False,
    urgent: bool = False,
    urgent_draft: str | None = None,
    meeting: Meeting | None = None,
) -> Decision:
    return Decision(
        label=label,
        urgent=urgent,
        finance=finance,
        meeting=meeting,
        reply=reply,
        urgent_draft=urgent_draft,
        summary="s",
        suspected_injection=None,
        decided_by=DecidedBy.MODEL,
        rules_fired=(),
        dropped=(),
    )


def plan(
    chosen: Decision,
    *,
    facts: EmailFacts | None = None,
    meeting: MeetingOutcome | None = None,
    sales_decline_needs_approval: bool = False,
    reply_block: str | None = None,
    attempt: int = 0,
) -> ActionPlan:
    return plan_actions(
        PolicyContext(
            decision=chosen,
            facts=facts or make_facts(),
            meeting=meeting,
            settings=PolicySettings(sales_decline_needs_approval=sales_decline_needs_approval),
            reply_block=reply_block,
            attempt=attempt,
        )
    )


def shape(actions: tuple[Action, ...]) -> list[tuple[str, str]]:
    return [(action.verb.value, action.args.get("label") or action.args.get("template") or "")
            for action in actions]  # fmt: skip


MEETING = Meeting(title="Sync", duration_minutes=None, windows=())


def test_marketing_is_archived_and_may_be_acknowledged() -> None:
    result = plan(decision(Label.MARKETING, reply=ReplyKind.ACKNOWLEDGEMENT))

    assert shape(result.automatic) == [
        ("apply_label", "MARKETING"), ("send_reply", "acknowledgement"), ("archive", ""),
    ]  # fmt: skip
    assert result.for_approval == ()


def test_marketing_without_a_warranted_reply_is_labelled_and_archived() -> None:
    result = plan(decision(Label.MARKETING))

    assert shape(result.automatic) == [("apply_label", "MARKETING"), ("archive", "")]


def test_marketing_from_a_bulk_sender_gets_no_acknowledgement() -> None:
    result = plan(
        decision(Label.MARKETING, reply=ReplyKind.ACKNOWLEDGEMENT),
        facts=make_facts(bulk=True, automated=True),
        reply_block=AUTOMATED_SENDER,
    )

    assert shape(result.automatic) == [("apply_label", "MARKETING"), ("archive", "")]


def test_sales_is_declined_then_archived_without_approval() -> None:
    result = plan(decision(Label.SALES_OUTREACH))

    assert shape(result.automatic) == [
        ("apply_label", "SALES_OUTREACH"), ("send_reply", "decline"), ("archive", ""),
    ]  # fmt: skip
    assert result.for_approval == ()


def test_sales_decline_waits_when_the_setting_is_on() -> None:
    result = plan(decision(Label.SALES_OUTREACH), sales_decline_needs_approval=True)

    assert shape(result.automatic) == [("apply_label", "SALES_OUTREACH"), ("archive", "")]
    assert shape(result.for_approval) == [("send_reply", "decline")]


def test_a_sales_decline_that_waits_is_still_refused_to_a_forged_sender() -> None:
    result = plan(
        decision(Label.SALES_OUTREACH),
        sales_decline_needs_approval=True,
        reply_block=DMARC_FAILED,
    )

    assert result.for_approval == ()


def test_a_sales_decline_that_waits_may_go_to_an_automated_sender_once_approved() -> None:
    result = plan(
        decision(Label.SALES_OUTREACH),
        sales_decline_needs_approval=True,
        reply_block=AUTOMATED_SENDER,
    )

    assert shape(result.for_approval) == [("send_reply", "decline")]


def test_task_gets_its_label_and_may_be_acknowledged() -> None:
    labelled = plan(decision(Label.TASK))
    acknowledged = plan(decision(Label.TASK, reply=ReplyKind.ACKNOWLEDGEMENT))

    assert shape(labelled.automatic) == [("apply_label", "TASK")]
    assert shape(acknowledged.automatic) == [
        ("apply_label", "TASK"), ("send_reply", "acknowledgement"),
    ]  # fmt: skip


def test_an_invoice_gets_finance_then_archive() -> None:
    result = plan(decision(Label.INFO_ONLY, finance=True, reply=ReplyKind.THANK_YOU))

    assert shape(result.automatic) == [
        ("apply_label", "INFO_ONLY"), ("apply_label", "Finance"), ("archive", ""),
    ]  # fmt: skip


def test_info_only_may_be_thanked() -> None:
    thanked = plan(decision(Label.INFO_ONLY, reply=ReplyKind.THANK_YOU))
    plain = plan(decision(Label.INFO_ONLY))

    assert shape(thanked.automatic) == [("apply_label", "INFO_ONLY"), ("send_reply", "thank_you")]
    assert shape(plain.automatic) == [("apply_label", "INFO_ONLY")]


def test_spam_moves_to_spam_and_never_gets_a_reply() -> None:
    result = plan(decision(Label.SPAM, reply=ReplyKind.ACKNOWLEDGEMENT), reply_block="spam")

    assert shape(result.automatic) == [("move_to_spam", "")]
    assert result.for_approval == ()


def test_unknown_gets_its_label_only() -> None:
    assert shape(plan(decision(Label.UNKNOWN)).automatic) == [("apply_label", "UNKNOWN")]


def test_a_free_slot_asks_one_approval_for_the_event_then_the_confirmation() -> None:
    result = plan(decision(Label.MEETING_REQUEST, meeting=MEETING), meeting=SlotFree(SLOT, False))

    assert shape(result.automatic) == [("apply_label", "MEETING_REQUEST")]
    assert shape(result.for_approval) == [("create_event", ""), ("send_reply", "confirmation")]
    assert result.holds == (SLOT,)


def test_the_slot_travels_in_the_args_with_its_zone_name_and_no_recipient() -> None:
    result = plan(decision(Label.MEETING_REQUEST, meeting=MEETING), meeting=SlotFree(SLOT, False))

    for action in result.for_approval:
        assert action.args["start"] == "2026-11-10T15:00:00+02:00"
        assert action.args["end"] == "2026-11-10T15:30:00+02:00"
        assert action.args["zone"] == "Europe/Bucharest"
        assert "to" not in action.args and "recipient" not in action.args


def test_no_free_slot_asks_approval_for_the_alternatives() -> None:
    result = plan(decision(Label.MEETING_REQUEST, meeting=MEETING), meeting=NoSlotFree(THREE_SLOTS))

    assert shape(result.for_approval) == [("send_reply", "alternatives")]
    assert result.holds == THREE_SLOTS


def test_no_alternatives_at_all_leaves_the_label_only() -> None:
    result = plan(decision(Label.MEETING_REQUEST, meeting=MEETING), meeting=NoSlotFree(()))

    assert result.for_approval == () and result.holds == ()


def test_an_invitation_already_on_the_calendar_gets_its_label_only() -> None:
    result = plan(
        decision(Label.MEETING_REQUEST, meeting=MEETING), meeting=AlreadyOnCalendar("evt-1")
    )

    assert shape(result.automatic) == [("apply_label", "MEETING_REQUEST")]
    assert result.for_approval == () and result.holds == ()


INTERNAL = make_facts(internal=True)
DRAFT = "On it, I will call you."


def test_urgent_internal_is_starred_labelled_and_its_draft_waits() -> None:
    result = plan(
        decision(Label.TASK, urgent=True, urgent_draft=DRAFT),
        facts=INTERNAL,
    )

    assert shape(result.automatic) == [
        ("apply_label", "TASK"),
        ("star", ""),
        ("apply_label", "URGENT"),
    ]
    assert shape(result.for_approval) == [("send_reply", "urgent_reply")]
    assert result.for_approval[0].args["text"] == DRAFT
    assert all(action.verb is not Verb.NOTIFY_MANAGER for action in result.automatic)


def test_urgent_internal_without_a_draft_is_notified_without_approval() -> None:
    result = plan(decision(Label.TASK, urgent=True), facts=INTERNAL)

    assert shape(result.automatic) == [
        ("apply_label", "TASK"), ("star", ""), ("apply_label", "URGENT"), ("notify_manager", ""),
    ]  # fmt: skip
    assert result.for_approval == ()


def test_urgent_external_is_starred_and_labelled_only() -> None:
    result = plan(decision(Label.TASK, urgent=True), facts=make_facts(internal=False))

    assert shape(result.automatic) == [
        ("apply_label", "TASK"),
        ("star", ""),
        ("apply_label", "URGENT"),
    ]
    assert result.for_approval == ()


def test_urgent_never_combines_with_spam() -> None:
    result = plan(decision(Label.SPAM, urgent=True), facts=INTERNAL, reply_block="spam")

    assert shape(result.automatic) == [("move_to_spam", "")]


def test_an_urgent_draft_to_an_automated_sender_is_dropped_but_the_notice_goes() -> None:
    result = plan(
        decision(Label.TASK, urgent=True, urgent_draft=DRAFT),
        facts=make_facts(internal=True, automated=True),
        reply_block=AUTOMATED_SENDER,
    )

    assert result.for_approval == ()
    assert shape(result.automatic)[-1] == ("notify_manager", "")


def test_an_urgent_internal_meeting_asks_one_approval_in_order() -> None:
    result = plan(
        decision(Label.MEETING_REQUEST, meeting=MEETING, urgent=True, urgent_draft=DRAFT),
        facts=INTERNAL,
        meeting=SlotFree(SLOT, False),
    )

    assert shape(result.for_approval) == [
        ("create_event", ""), ("send_reply", "confirmation"), ("send_reply", "urgent_reply"),
    ]  # fmt: skip
    assert result.holds == (SLOT,)


def test_with_an_empty_internal_list_every_urgent_sender_counts_as_internal() -> None:
    settings = FactSettings(ACCOUNT_ADDRESS, frozenset(), frozenset())
    facts = extract_facts(make_raw(sender="stranger@elsewhere.example", dmarc="pass"), settings)

    result = plan(decision(Label.TASK, urgent=True, urgent_draft=DRAFT), facts=facts)

    assert shape(result.for_approval) == [("send_reply", "urgent_reply")]


def test_a_forged_internal_address_gets_no_notice_path() -> None:
    settings = FactSettings(ACCOUNT_ADDRESS, frozenset({"boss@company.com"}), frozenset())
    facts = extract_facts(make_raw(sender="boss@company.com", dmarc="fail"), settings)

    assert not facts.internal
    result = plan(decision(Label.TASK, urgent=True), facts=facts)

    assert all(action.verb is not Verb.NOTIFY_MANAGER for action in result.automatic)


def test_a_new_attempt_gives_gated_actions_new_keys_and_automatic_ones_the_same() -> None:
    chosen = decision(Label.SALES_OUTREACH)
    first = plan(chosen, sales_decline_needs_approval=True, attempt=0)
    second = plan(chosen, sales_decline_needs_approval=True, attempt=1)

    assert first.for_approval[0].key != second.for_approval[0].key
    assert [a.key for a in first.automatic] == [a.key for a in second.automatic]


def test_keys_are_unique_within_a_plan() -> None:
    result = plan(
        decision(Label.INFO_ONLY, finance=True, urgent=True, urgent_draft=DRAFT), facts=INTERNAL
    )
    keys = [action.key for action in (*result.automatic, *result.for_approval)]

    assert len(keys) == len(set(keys))


def test_keys_derive_from_the_source_message() -> None:
    one = plan(decision(Label.TASK), facts=make_facts(message_id="m-1"))
    two = plan(decision(Label.TASK), facts=make_facts(message_id="m-2"))

    assert one.automatic[0].key != two.automatic[0].key


def test_inbox_removing_verbs_run_last() -> None:
    inbox_removers = {Verb.ARCHIVE, Verb.MOVE_TO_SPAM}
    for label in Label:
        result = plan(
            decision(label, finance=True, urgent=True, reply=ReplyKind.NONE),
            facts=INTERNAL,
            reply_block="spam" if label is Label.SPAM else None,
        )
        verbs = [action.verb for action in result.automatic]
        tail = verbs[len(verbs) - sum(v in inbox_removers for v in verbs) :]
        assert all(v in inbox_removers for v in tail), label


SELF_SENDER = make_facts(sender=ACCOUNT_ADDRESS, internal=True)
OWN_ACCOUNT_BLOCK = "the sender is the account itself"


def test_a_meeting_from_the_account_itself_still_asks_for_the_event_then_the_confirmation() -> None:
    result = plan(
        decision(Label.MEETING_REQUEST, meeting=MEETING),
        facts=SELF_SENDER,
        meeting=SlotFree(SLOT, False),
        reply_block=OWN_ACCOUNT_BLOCK,
    )

    assert shape(result.for_approval) == [("create_event", ""), ("send_reply", "confirmation")]
    assert result.holds == (SLOT,)


def test_a_sales_decline_from_the_account_itself_waits_for_approval_when_the_setting_is_on() -> (
    None
):
    result = plan(
        decision(Label.SALES_OUTREACH),
        facts=SELF_SENDER,
        sales_decline_needs_approval=True,
        reply_block=OWN_ACCOUNT_BLOCK,
    )

    assert shape(result.for_approval) == [("send_reply", "decline")]


def test_an_urgent_draft_to_the_account_itself_is_kept_and_gated_without_a_separate_notice() -> (
    None
):
    result = plan(
        decision(Label.TASK, urgent=True, urgent_draft=DRAFT),
        facts=SELF_SENDER,
        reply_block=OWN_ACCOUNT_BLOCK,
    )

    assert shape(result.for_approval) == [("send_reply", "urgent_reply")]
    assert all(action.verb is not Verb.NOTIFY_MANAGER for action in result.automatic)


def test_an_automatic_reply_to_the_account_itself_is_still_refused() -> None:
    result = plan(
        decision(Label.TASK, reply=ReplyKind.ACKNOWLEDGEMENT),
        facts=SELF_SENDER,
        reply_block=OWN_ACCOUNT_BLOCK,
    )

    assert shape(result.automatic) == [("apply_label", "TASK")]


@pytest.mark.parametrize(
    "outcome", [SlotFree(SLOT, False), NoSlotFree(THREE_SLOTS)], ids=["free-slot", "alternatives"]
)
def test_a_meeting_from_a_forged_sender_gets_its_label_only(outcome: MeetingOutcome) -> None:
    result = plan(
        decision(Label.MEETING_REQUEST, meeting=MEETING),
        meeting=outcome,
        reply_block=DMARC_FAILED,
    )

    assert shape(result.automatic) == [("apply_label", "MEETING_REQUEST")]
    assert result.for_approval == () and result.holds == ()


def test_every_step_names_an_existing_predicate_and_verb() -> None:
    steps = [step for steps in POLICY_STEPS.values() for step in steps] + list(URGENT_STEPS)

    assert steps
    for step in steps:
        assert step.when in PREDICATES
        assert Verb(step.verb) in Verb
        assert step.approval in {"none", "required"} or step.approval.startswith("setting:")
        if step.approval.startswith("setting:"):
            setting = step.approval.removeprefix("setting:").lower()
            assert setting in {f.name for f in fields(PolicySettings)}


def test_every_label_has_a_record() -> None:
    assert set(POLICY_STEPS) == set(Label)


@pytest.mark.parametrize("label", list(Label))
def test_a_plan_never_addresses_a_recipient(label: Label) -> None:
    result = plan(decision(label, reply=ReplyKind.NONE), facts=INTERNAL)

    for action in (*result.automatic, *result.for_approval):
        assert not {"to", "recipient", "address"} & set(action.args)
