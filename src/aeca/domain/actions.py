from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Verb(StrEnum):
    APPLY_LABEL = "apply_label"
    ARCHIVE = "archive"
    MOVE_TO_SPAM = "move_to_spam"
    STAR = "star"
    SEND_REPLY = "send_reply"
    NOTIFY_MANAGER = "notify_manager"
    REQUEST_APPROVAL = "request_approval"
    CREATE_EVENT = "create_event"


class ActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Action:
    verb: Verb
    key: str
    args: Mapping[str, str]

    # A frozen dataclass still lets a dict inside it change, and a changed action no longer
    # matches the key it was journalled under.
    def __post_init__(self) -> None:
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))


@dataclass(frozen=True)
class ActionRecord:
    action: Action
    source_message_id: str
    approval_id: str | None
    status: ActionStatus
    ref: str | None
