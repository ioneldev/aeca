"""Reads the real account. Needs GOOGLE_REFRESH_TOKEN; run with
`uv run --env-file .env pytest -m live -q`."""

from datetime import UTC, datetime, timedelta

import pytest

from aeca.adapters.google_services import GoogleServices, build_google_services
from aeca.domain.time_range import Interval
from aeca.settings import load_settings

pytestmark = pytest.mark.live


@pytest.fixture
def services(live_run_env: dict[str, str]) -> GoogleServices:
    return build_google_services(load_settings(live_run_env, purpose="run"))


def test_the_profile_has_an_address(services: GoogleServices) -> None:
    address = services.mailbox.address()

    assert "@" in address


def test_the_inbox_can_be_listed(services: GoogleServices) -> None:
    ids = services.mailbox.unprocessed_inbox(limit=5)

    assert len(ids) <= 5


def test_the_calendar_has_a_zone_and_lists_today(services: GoogleServices) -> None:
    zone = services.calendar.time_zone()
    now = datetime.now(UTC)

    events = services.calendar.events(Interval(now, now + timedelta(days=1)))

    assert "/" in zone or zone == "UTC"
    assert isinstance(events, list)
