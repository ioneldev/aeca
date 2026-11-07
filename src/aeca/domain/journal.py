from dataclasses import dataclass

from aeca.domain.actions import Action


@dataclass(frozen=True)
class JournalEntry:
    source_message_id: str
    decision_json: str
    actions: tuple[Action, ...]
    # Kept in the journal so a resume can reopen the approval, and never registered as actions:
    # nothing may run them before the manager approves.
    awaiting_approval: tuple[Action, ...] = ()
