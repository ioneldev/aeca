"""An audit line for a test that only cares which email it is about, and how it ended."""

from decimal import Decimal

from fakes.fixed_clock import FIXED_NOW

from aeca.domain.audit import AuditLine


def line_for(message_id: str, outcome: str = "done") -> AuditLine:
    return AuditLine(
        time=FIXED_NOW,
        message_id=message_id,
        thread_id="t",
        sender="s",
        subject="x",
        label="TASK",
        urgent=False,
        decided_by="rule",
        rules=(),
        actions=(),
        approval_id=None,
        approval_state=None,
        outcome=outcome,
        error=None,
        tokens_in=0,
        tokens_out=0,
        usd=Decimal(0),
    )
