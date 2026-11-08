import re
from datetime import date

from aeca.domain.ids import action_key, alert_id, approval_id, event_id

EVENT_ID_ALPHABET = re.compile(r"^[a-v0-9]{5,1024}$")


def test_event_id_alphabet_and_stability() -> None:
    first = event_id("gmail-1", 0)

    assert EVENT_ID_ALPHABET.match(first)
    assert first.startswith("aeca") and len(first) == 4 + 26
    assert first == event_id("gmail-1", 0)


def test_ids_differ_by_attempt() -> None:
    assert event_id("gmail-1", 0) != event_id("gmail-1", 1)
    assert approval_id("gmail-1", 0) != approval_id("gmail-1", 1)


def test_ids_differ_by_source_message() -> None:
    assert action_key("gmail-1", "archive") != action_key("gmail-2", "archive")
    assert action_key("gmail-1", "archive") != action_key("gmail-1", "star")


def test_ids_have_their_documented_shape() -> None:
    assert re.fullmatch(r"k-[0-9a-f]{16}", action_key("gmail-1", "archive"))
    assert re.fullmatch(r"a-[0-9a-f]{12}", approval_id("gmail-1", 0))


def test_an_alert_id_is_the_same_for_one_incident_kind_on_one_day() -> None:
    assert alert_id("cost_cap", date(2026, 11, 7)) == alert_id("cost_cap", date(2026, 11, 7))
    assert alert_id("cost_cap", date(2026, 11, 7)) != alert_id("cost_cap", date(2026, 11, 8))
    assert alert_id("cost_cap", date(2026, 11, 7)) != alert_id("key_refused", date(2026, 11, 7))
    assert re.fullmatch(r"al-[0-9a-f]{12}", alert_id("cost_cap", date(2026, 11, 7)))
