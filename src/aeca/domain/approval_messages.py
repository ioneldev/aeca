"""The two messages an approval sends besides its own actions: the request to the manager, and
the help reply to an answer AECA cannot read. Each is an action, so it goes through the executor's
draft path and is sent exactly once.

The request's args carry everything its text is written from, so a resume writes the same request
the first run would have.
"""

from dataclasses import dataclass

from aeca.domain.actions import Action, Verb, actions_from_json, actions_to_json
from aeca.domain.approval import Approval
from aeca.domain.ids import action_key

HELP_TEMPLATE = "help"
YES, NO = "yes", "no"


@dataclass(frozen=True, slots=True)
class RequestArgs:
    to: str
    summary: str
    actions: tuple[Action, ...]
    outside_work_hours: bool

    @staticmethod
    def of(action: Action) -> "RequestArgs":
        args = action.args
        return RequestArgs(
            to=args["to"],
            summary=args["summary"],
            actions=actions_from_json(args["actions"]),
            outside_work_hours=args["outside_work_hours"] == YES,
        )


# Keyed by the approval id, which is also the request's X-AECA-Id: one request per approval.
def request_action(approval: Approval, *, to: str, outside_work_hours: bool) -> Action:
    return Action(
        verb=Verb.REQUEST_APPROVAL,
        key=approval.id,
        args={
            "to": to,
            "summary": approval.summary,
            "actions": actions_to_json(approval.actions),
            "outside_work_hours": YES if outside_work_hours else NO,
        },
    )


# One per approval: a second unclear answer finds it done and gets no second help reply.
def help_action(approval: Approval) -> Action:
    return Action(
        verb=Verb.SEND_REPLY,
        key=action_key(approval.source_message_id, f"help@{approval.attempt}"),
        args={"template": HELP_TEMPLATE},
    )
