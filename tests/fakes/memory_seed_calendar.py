from aeca.adapters.google_seed_calendar import SeedInsert
from aeca.tools.seed_calendar import SeedEvent


class MemorySeedCalendar:
    """A SeedCalendar that keeps the events it was given, and refuses an id it already holds the
    way Google does."""

    def __init__(self) -> None:
        self.events: list[SeedEvent] = []

    def insert(self, event: SeedEvent) -> SeedInsert:
        already_held = any(held.event_id == event.event_id for held in self.events)
        if not already_held:
            self.events.append(event)
        return SeedInsert(event_id=event.event_id, created=not already_held)
