# 0031: Every action goes through one journal, and sends go through drafts

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- Before the first side effect, AECA stores the decision and its planned actions, each with its key. Each action is marked done as it finishes.
- After a restart, unfinished work resumes from the journal by message id. At startup, every action left `executing` is resolved by checking its key.
- Sends go through Gmail drafts: create the draft, record its id, send it. After a restart, a recorded draft that still exists is sent; one that no longer exists was already sent.
- Before a reply, AECA reads the thread with `threads.get`. A sent message with the same `X-AECA-Id` means it doesn't send.
- A failed approved action is retried with the same keys before it is marked `failed`.
- `AECA/processed` is added last. While an approval is open, `AECA/awaiting` stays on.

**Why.** Resume runs from the journal, not the inbox query, because an archived email no longer matches the query. `threads.get` reads the thread directly, unlike search, whose index can lag. The thread check also covers a lost journal and a second copy.

**Rejected.**
- Resuming from the inbox query.
- Searching for the sent message, because the index can lag.
- Sending without a draft (inferred). There would be no record to check after a crash.

**Cost.** The spec states none. The tech-lead review made crash-between-write-and-state-write cases block a release.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.5, §4.6 and §5.
