from dataclasses import dataclass

from aeca.domain.actions import Action


@dataclass(frozen=True)
class JournalEntry:
    source_message_id: str
    decision_json: str
    actions: tuple[Action, ...]
