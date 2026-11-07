from typing import Any
from zoneinfo import ZoneInfo

import pytest
from support.facts import NOW, make_facts

from aeca.domain.decision import DecidedBy, Decision, ModelDecision, ModelMeeting, ModelWindow
from aeca.domain.labels import Label, ReplyKind
from aeca.domain.validation import LabelRefused, ValidationContext, validate

BUCHAREST = ZoneInfo("Europe/Bucharest")
FUTURE_START = "2026-11-10T15:00:00+02:00"


def context(**fact_fields: Any) -> ValidationContext:
    return ValidationContext(facts=make_facts(**fact_fields), now=NOW, zone=BUCHAREST)


def decision(primary_label: str = "TASK", **fields: Any) -> ModelDecision:
    return ModelDecision(primary_label=primary_label, summary="a summary", **fields)


def meeting_decision(*windows: ModelWindow, **meeting_fields: Any) -> ModelDecision:
    meeting = ModelMeeting(title="Sync", windows=list(windows), **meeting_fields)
    return decision("MEETING_REQUEST", meeting=meeting)


def window(quote: str = "Tuesday at 3pm", start: str = FUTURE_START, **fields: Any) -> ModelWindow:
    return ModelWindow(start=start, quote=quote, **fields)


def validated(submitted: ModelDecision, ctx: ValidationContext) -> Decision:
    result = validate(submitted, ctx)
    assert isinstance(result, Decision)
    return result


# --- the label ---


def test_an_unknown_label_is_refused_with_a_reason_the_model_can_read() -> None:
    result = validate(decision("NEWS"), context())

    assert result == LabelRefused(reason="NEWS is not a label")


@pytest.mark.parametrize("submitted", ["", "   "])
def test_a_missing_label_is_refused(submitted: str) -> None:
    assert validate(decision(submitted), context()) == LabelRefused(
        reason="the primary label is missing"
    )


@pytest.mark.parametrize("submitted", ["task", " TASK ", "Task"])
def test_a_label_matches_case_insensitively_after_trimming(submitted: str) -> None:
    assert validated(decision(submitted), context()).label is Label.TASK


def test_a_valid_decision_is_decided_by_the_model_with_nothing_dropped() -> None:
    result = validated(decision("INFO_ONLY", finance=True), context())

    assert (result.decided_by, result.rules_fired, result.dropped) == (DecidedBy.MODEL, (), ())
    assert result.finance is True


# --- urgent ---


def test_urgent_is_dropped_on_spam() -> None:
    ctx = context(text="Act now or lose it")

    result = validated(decision("SPAM", urgent=True, urgent_quote="act now"), ctx)

    assert not result.urgent and "urgent" in result.dropped[0]
    assert result.label is Label.SPAM


def test_urgent_is_kept_when_its_quote_is_in_the_email() -> None:
    ctx = context(text="The server is down. We need you now.")

    result = validated(decision(urgent=True, urgent_quote="the server is down"), ctx)

    assert result.urgent and result.dropped == ()


def test_urgent_without_a_matching_quote_is_dropped() -> None:
    result = validated(decision(urgent=True, urgent_quote="the building is on fire"), context())

    assert not result.urgent
    assert result.label is Label.TASK
    assert "urgent" in result.dropped[0]


@pytest.mark.parametrize("quote", [None, "", "   "])
def test_urgent_with_an_empty_quote_is_dropped(quote: str | None) -> None:
    result = validated(decision(urgent=True, urgent_quote=quote), context())

    assert not result.urgent and "urgent" in result.dropped[0]


def test_a_kept_urgency_keeps_its_quote_for_the_manager() -> None:
    ctx = context(text="Our server is down since 9.")

    result = validated(decision(urgent=True, urgent_quote="  server is down "), ctx)

    assert result.urgent_quote == "server is down"


def test_a_dropped_urgency_keeps_no_quote() -> None:
    result = validated(decision(urgent=True, urgent_quote="made up"), context())

    assert result.urgent_quote is None


def test_an_urgent_quote_may_come_from_the_subject() -> None:
    ctx = context(subject="Outage in production", text="Please look.")

    assert validated(decision(urgent=True, urgent_quote="outage in production"), ctx).urgent


# --- quotes ---


def test_a_window_quote_missing_from_the_email_is_dropped() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(quote="Wednesday at 4pm")), ctx)

    assert result.label is Label.MEETING_REQUEST
    assert result.meeting is not None and result.meeting.windows == ()
    assert "meeting.windows[0]" in result.dropped[0]


@pytest.mark.parametrize(
    ("email_text", "quote"),
    [
        ("Does \u201c3 pm\u201d work?", '"3  pm"'),
        ("Does \u201e3 pm\u201c work?", '"3 pm"'),
        ("Does \u20183 pm\u2019 work?", "'3 pm'"),
        ('Does "3 pm" work?', "\u201c3 pm\u201d"),
    ],
)
def test_quotes_compare_after_normalising_whitespace_and_quote_marks(
    email_text: str, quote: str
) -> None:
    result = validated(meeting_decision(window(quote=quote)), context(text=email_text))

    assert result.meeting is not None and len(result.meeting.windows) == 1


def test_quotes_compare_ignoring_case() -> None:
    ctx = context(text="TUESDAY AT 3PM")

    result = validated(meeting_decision(window(quote="tuesday at 3pm")), ctx)

    assert result.meeting is not None and len(result.meeting.windows) == 1


def test_quotes_compare_across_line_breaks() -> None:
    ctx = context(text="Tuesday\nat 3pm")

    result = validated(meeting_decision(window(quote="Tuesday at 3pm")), ctx)

    assert result.meeting is not None and len(result.meeting.windows) == 1


def test_a_window_quote_may_come_from_the_subject() -> None:
    ctx = context(subject="Call on Tuesday at 3pm?", text="Let me know.")

    result = validated(meeting_decision(window(quote="Tuesday at 3pm")), ctx)

    assert result.meeting is not None and len(result.meeting.windows) == 1


@pytest.mark.parametrize("quote", ["", "   "])
def test_an_empty_quote_never_matches(quote: str) -> None:
    result = validated(meeting_decision(window(quote=quote)), context())

    assert result.meeting is not None and result.meeting.windows == ()


# --- times ---


def test_a_past_time_is_dropped_and_the_label_kept() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(start="2026-11-01T10:00:00+02:00")), ctx)

    assert result.label is Label.MEETING_REQUEST
    assert result.meeting is not None and result.meeting.windows == ()
    assert result.dropped == ("meeting.windows[0]: start is in the past",)


def test_an_unparseable_time_is_dropped() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(start="next Tuesday")), ctx)

    assert result.meeting is not None and result.meeting.windows == ()
    assert result.dropped == ("meeting.windows[0]: start is not a time",)


def test_a_naive_time_is_read_in_the_calendar_zone() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(start="2026-11-10T15:00")), ctx)

    assert result.meeting is not None
    assert result.meeting.windows[0].start.tzinfo == BUCHAREST
    assert result.meeting.windows[0].start.utcoffset() is not None


def test_an_aware_time_keeps_its_own_zone() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(start="2026-11-10T15:00:00+01:00")), ctx)

    assert result.meeting is not None
    assert result.meeting.windows[0].start.utcoffset().total_seconds() == 3600  # type: ignore[union-attr]


def test_a_naive_time_just_before_now_in_the_calendar_zone_is_in_the_past() -> None:
    ctx = context(text="Tuesday at 3pm")
    # NOW is 12:00 UTC, which is 14:00 in Bucharest, so 13:00 local has already passed.
    result = validated(meeting_decision(window(start="2026-11-07T13:00")), ctx)

    assert result.meeting is not None and result.meeting.windows == ()


def test_an_end_before_the_start_drops_the_window() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(end="2026-11-10T14:00:00+02:00")), ctx)

    assert result.meeting is not None and result.meeting.windows == ()
    assert "meeting.windows[0]: end is not after start" in result.dropped


def test_an_end_equal_to_the_start_drops_the_window() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(end=FUTURE_START)), ctx)

    assert result.meeting is not None and result.meeting.windows == ()


def test_an_unparseable_end_drops_only_the_end() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(end="around four")), ctx)

    assert result.meeting is not None
    assert [(w.end, w.quote) for w in result.meeting.windows] == [(None, "Tuesday at 3pm")]
    assert result.dropped == ("meeting.windows[0]: end is not a time",)


def test_a_valid_end_is_kept() -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(end="2026-11-10T16:00:00+02:00")), ctx)

    assert result.meeting is not None and result.meeting.windows[0].end is not None


def test_a_dropped_window_note_names_its_original_position() -> None:
    ctx = context(text="Tuesday at 3pm or Wednesday at 4pm")
    good = window(quote="Wednesday at 4pm")
    bad = window(quote="Tuesday at 3pm", start="2026-11-01T10:00:00+02:00")

    result = validated(meeting_decision(bad, good), ctx)

    assert result.meeting is not None and len(result.meeting.windows) == 1
    assert result.dropped == ("meeting.windows[0]: start is in the past",)


# --- meetings ---


def test_a_meeting_with_no_valid_window_stays_a_meeting_with_no_windows() -> None:
    result = validated(meeting_decision(window(quote="nowhere in the email")), context())

    assert result.label is Label.MEETING_REQUEST
    assert result.meeting is not None
    assert (result.meeting.title, result.meeting.windows) == ("Sync", ())


def test_a_meeting_request_with_no_meeting_block_takes_the_subject_as_title() -> None:
    ctx = context(subject="Quick call?")

    result = validated(decision("MEETING_REQUEST"), ctx)

    assert result.meeting is not None
    assert (result.meeting.title, result.meeting.duration_minutes, result.meeting.windows) == (
        "Quick call?",
        None,
        (),
    )


def test_a_meeting_block_on_another_label_is_dropped_and_noted() -> None:
    submitted = decision("TASK", meeting=ModelMeeting(title="Sync", windows=[]))

    result = validated(submitted, context())

    assert result.meeting is None and result.label is Label.TASK
    assert "meeting" in result.dropped[0]


@pytest.mark.parametrize("minutes", [4, 481, 0, -30])
def test_a_duration_outside_five_to_four_hundred_eighty_is_dropped(minutes: int) -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(), duration_minutes=minutes), ctx)

    assert result.meeting is not None and result.meeting.duration_minutes is None
    assert "meeting.duration_minutes" in result.dropped[0]


@pytest.mark.parametrize("minutes", [5, 45, 480])
def test_a_duration_inside_the_range_is_kept(minutes: int) -> None:
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(window(), duration_minutes=minutes), ctx)

    assert result.meeting is not None and result.meeting.duration_minutes == minutes


# --- replies ---


def test_a_reply_kind_the_label_does_not_allow_becomes_none() -> None:
    result = validated(decision("TASK", reply=ReplyKind.DECLINE), context())

    assert result.reply is ReplyKind.NONE and result.label is Label.TASK
    assert "reply" in result.dropped[0]


def test_a_reply_kind_the_label_allows_is_kept() -> None:
    result = validated(decision("SALES_OUTREACH", reply=ReplyKind.DECLINE), context())

    assert result.reply is ReplyKind.DECLINE and result.dropped == ()


def test_no_reply_is_ever_allowed_on_spam() -> None:
    result = validated(decision("SPAM", reply=ReplyKind.ACKNOWLEDGEMENT), context())

    assert result.reply is ReplyKind.NONE


def test_asking_for_no_reply_is_never_a_drop() -> None:
    assert validated(decision("SPAM"), context()).dropped == ()


# --- the urgent draft ---


def urgent_draft_decision(draft: str | None) -> ModelDecision:
    return decision(urgent=True, urgent_quote="need it today", urgent_draft=draft)


def internal_urgent_context() -> ValidationContext:
    return context(text="We need it today", internal=True)


def test_an_urgent_draft_survives_when_urgent_and_internal() -> None:
    result = validated(urgent_draft_decision("On it, I will call you."), internal_urgent_context())

    assert result.urgent_draft == "On it, I will call you."


def test_an_urgent_draft_is_dropped_for_an_external_sender() -> None:
    ctx = context(text="We need it today", internal=False)

    result = validated(urgent_draft_decision("On it."), ctx)

    assert result.urgent and result.urgent_draft is None
    assert "urgent_draft" in result.dropped[0]


def test_an_urgent_draft_is_dropped_when_the_email_is_not_urgent() -> None:
    ctx = context(text="Nothing special", internal=True)

    result = validated(decision(urgent=False, urgent_draft="On it."), ctx)

    assert result.urgent_draft is None and "urgent_draft" in result.dropped[0]


def test_an_urgent_draft_is_dropped_when_urgent_was_dropped_for_a_bad_quote() -> None:
    ctx = context(text="Nothing special", internal=True)

    result = validated(decision(urgent=True, urgent_quote="made up", urgent_draft="On it."), ctx)

    assert not result.urgent and result.urgent_draft is None


@pytest.mark.parametrize(
    "draft",
    [
        "See https://example.com for details",
        "See HTTP://EXAMPLE.COM for details",
        "See www.example.com for details",
        "Write to boss@example.com",
        "a" * 801,
    ],
)
def test_an_urgent_draft_with_a_link_an_address_or_801_characters_is_dropped(draft: str) -> None:
    result = validated(urgent_draft_decision(draft), internal_urgent_context())

    assert result.urgent and result.urgent_draft is None
    assert "urgent_draft" in result.dropped[0]


def test_an_urgent_draft_of_exactly_800_characters_survives() -> None:
    result = validated(urgent_draft_decision("a" * 800), internal_urgent_context())

    assert result.urgent_draft == "a" * 800


def test_a_blank_urgent_draft_is_no_draft() -> None:
    result = validated(urgent_draft_decision("   "), internal_urgent_context())

    assert result.urgent_draft is None


# --- the summary and the suspected injection ---


def test_the_summary_is_bounded_to_200_characters() -> None:
    result = validated(ModelDecision(primary_label="TASK", summary="x" * 500), context())

    assert result.summary == "x" * 200


def test_the_summary_escapes_control_characters() -> None:
    result = validated(ModelDecision(primary_label="TASK", summary="a\x1b[31mb"), context())

    assert "\x1b" not in result.summary


def test_the_suspected_injection_is_bounded_to_500_characters_and_escaped() -> None:
    submitted = decision(suspected_injection="\x1b" + "y" * 900)

    result = validated(submitted, context())

    assert result.suspected_injection is not None
    assert len(result.suspected_injection) == 500
    assert "\x1b" not in result.suspected_injection


def test_no_suspected_injection_stays_none() -> None:
    assert validated(decision(), context()).suspected_injection is None


# --- the fallback ---


def test_the_fallback_decision_is_unknown_and_changes_nothing() -> None:
    result = Decision.fallback("could not classify")

    assert result.label is Label.UNKNOWN
    assert result.decided_by is DecidedBy.FALLBACK
    assert (result.urgent, result.finance, result.meeting, result.reply) == (
        False,
        False,
        None,
        ReplyKind.NONE,
    )
    assert result.summary == "could not classify"


# --- a window must be a proposed time, not a stretch of days (ruling R46) ---

TOO_WIDE = "meeting.windows[0]: too wide to be a proposed time"


def windows_kept(*submitted: ModelWindow) -> tuple[Any, ...]:
    result = validated(meeting_decision(*submitted), context(text="Tuesday at 3pm"))
    assert result.meeting is not None
    return result.meeting.windows


def test_a_window_across_a_whole_week_is_dropped_and_noted() -> None:
    ctx = context(text="Tuesday at 3pm")

    week = window(start="2026-11-09T09:00:00+02:00", end="2026-11-13T17:00:00+02:00")

    result = validated(meeting_decision(week), ctx)

    assert result.meeting is not None and result.meeting.windows == ()
    assert result.dropped == (TOO_WIDE,)


def test_a_window_across_a_whole_day_is_dropped() -> None:
    whole_day = window(start="2026-11-10T00:00:00+02:00", end="2026-11-11T00:00:00+02:00")

    assert windows_kept(whole_day) == ()


def test_a_window_across_part_of_a_day_is_kept() -> None:
    afternoon = window(start="2026-11-10T12:00:00+02:00", end="2026-11-10T17:00:00+02:00")

    assert len(windows_kept(afternoon)) == 1


def test_a_window_without_an_end_is_kept() -> None:
    assert len(windows_kept(window())) == 1


def test_a_window_of_exactly_twelve_hours_is_kept_and_one_minute_more_is_dropped() -> None:
    twelve_hours = window(start="2026-11-10T08:00:00+02:00", end="2026-11-10T20:00:00+02:00")
    twelve_hours_and_a_minute = window(
        start="2026-11-10T08:00:00+02:00", end="2026-11-10T20:01:00+02:00"
    )

    assert len(windows_kept(twelve_hours)) == 1
    assert windows_kept(twelve_hours_and_a_minute) == ()


def test_the_width_is_measured_in_utc_when_the_clocks_go_forward() -> None:
    # Clocks go forward at 03:00 on 2027-03-28: 13 hours on the wall, 12 in real time.
    overnight = window(start="2027-03-27T20:00", end="2027-03-28T09:00")

    assert len(windows_kept(overnight)) == 1


def test_the_width_is_measured_in_utc_when_the_clocks_go_back() -> None:
    # Clocks go back at 04:00 on 2027-10-31: 12 hours on the wall, 13 in real time.
    overnight = window(start="2027-10-30T20:00", end="2027-10-31T08:00")

    assert windows_kept(overnight) == ()


def test_an_end_that_is_before_the_start_in_utc_drops_the_window() -> None:
    # 03:30 does not exist on 2027-03-28 in Bucharest: the clocks jump from 03:00 to 04:00, so
    # 03:30 reads as 01:30 UTC and 04:00 as 01:00 UTC. The wall clock says the end is later.
    spring_gap = window(start="2027-03-28T03:30", end="2027-03-28T04:00")
    ctx = context(text="Tuesday at 3pm")

    result = validated(meeting_decision(spring_gap), ctx)

    assert result.meeting is not None and result.meeting.windows == ()
    assert result.dropped == ("meeting.windows[0]: end is not after start",)


def test_an_urgent_draft_is_dropped_on_a_meeting_because_its_reply_is_the_reply() -> None:
    submitted = meeting_decision(window()).model_copy(
        update={
            "urgent": True,
            "urgent_quote": "need it today",
            "urgent_draft": "I can join at 14:00.",
        }
    )

    result = validated(submitted, context(text="We need it today, Tuesday at 3pm", internal=True))

    assert result.urgent and result.urgent_draft is None
    assert result.dropped == ("urgent_draft: a meeting's reply is the meeting reply",)
