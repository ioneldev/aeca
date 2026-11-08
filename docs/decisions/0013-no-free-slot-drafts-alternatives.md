# 0013: When no proposed time is free, the manager approves a reply with alternatives

**Who decided:** Claude. 2026-11-05.

**Decision.**
- When no proposed time is free, or none was proposed, the approval email lists up to 3 free slots and a reply that proposes them.
- The 3 slots become holds for that thread.
- APPROVE sends that reply. No event is created yet.
- The requester's answer arrives as a new email, and is planned like any other.

**Why.** The spec gives no further reason.

**Rejected.**
- Creating an event at the first free slot without asking (inferred).
- Doing nothing and leaving the email labelled (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.4.
