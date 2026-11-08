# 0039: The one-copy lock is a courtesy, not the guarantee

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- The lock lives in the Google account, since that is the only thing two copies on different machines share. The proposal is a Gmail draft titled `AECA lock` with a heartbeat, behind its own port.
- At startup a fresh heartbeat from another copy stops AECA and names the next step. `FORCE_LOCK` overrides it. On every tick AECA checks it still holds the lock.
- The guarantee is the keys derived from Gmail message ids: for events, the event id; for replies, the thread check.

**Why.** Gmail drafts have no compare-and-swap, so two copies starting at the same moment can both take the lock. Refusing to start in front of the evaluators would be worse than the risk the lock prevents.

**Rejected.**
- Treating the lock as the guarantee.
- A lock outside the Google account. Two copies on different machines would not share it.

**Cost.** One gap remains. A new approval request has no thread yet, so two copies racing at the same moment could each send one. The manager would see two requests, and the second APPROVE would find the event already created.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.12 and §11.
