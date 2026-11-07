# 0025: Holds keep two requesters apart, and one email is handled at a time

**Who decided:** Claude, after review for holds; Claude for one at a time. 2026-11-05.

**Decision.**
- A hold is a slot in an approval nobody has answered, or one of the alternatives offered to a requester.
- Holds are keyed by the email's thread. Other threads see the slot as busy. The thread's own holds count as free for that thread.
- Holds are released on REJECT, when the approval is superseded, once it is executed, or when the slot starts.
- AECA processes one email at a time. A small worker pool goes in only if the rehearsal shows it is too slow. The target is all 20 emails within 5 minutes.

**Why.** Two requesters must never be offered the same slot. The thread's own holds count as free so a reply of "the second slot works" still fits. Holds need a fixed order, which is why emails run one at a time.

**Rejected.**
- No holds, so two requesters could be offered the same slot.
- A worker pool from the start, which would break the fixed order holds need.

**Cost.** Speed. The rehearsal measures the burst of 20.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.4 and §4.6.
