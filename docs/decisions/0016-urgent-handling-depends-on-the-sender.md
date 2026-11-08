# 0016: An urgent email is handled by who sent it

**Changed in part by [0059](0059-an-urgent-meeting-gets-one-reply.md)** (2026-11-07): an urgent meeting request gets the meeting reply only, with no drafted reply beside it. What follows is kept as it was written.

**Who decided:** Ionel. 2026-11-05.

**Decision.**
- From a sender in `INTERNAL_SENDERS`: star it, add `URGENT`, send the manager a notice with a reply drafted to the sender. The reply waits for APPROVE.
- From anyone else: star it and add `URGENT`. No approval and no draft.
- A primary label's actions and the modifier's add up. An urgent internal TASK gets both rows.
- `URGENT` is never combined with SPAM.

**Why.** The spec gives no further reason. It follows the brief's urgent scenario.

**Rejected.**
- Drafting a reply for every urgent sender (inferred).
- No draft even for internal senders (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.3.
