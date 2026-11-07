from aeca.tools.seed_calendar import SeedEvent


class MemorySeedCalendar:
    """A SeedCalendar that keeps the events it was given, for a test to read back."""

    def __init__(self) -> None:
        self.events: list[SeedEvent] = []

    def insert(self, event: SeedEvent) -> str:
        self.events.append(event)
        return f"event-{len(self.events)}"
