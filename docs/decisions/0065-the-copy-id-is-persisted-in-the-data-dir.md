# 0065: The copy id is persisted in the data dir

**Who decided:** Claude (ruling R52), 2026-11-07.

**Decision.**
- The copy id is a random id written once to `copy-id` in the data directory, and read at every start.
- A restart of the same installation takes its own lock back at once. Another machine has another volume, so another id.
- A wiped volume is a new copy, which waits out the stale window.

**Why.** With an id new at every start, a container restart after a crash found its own lock fresh and refused to start, for up to 3 × `POLL_SECONDS`.

**Rejected.**
- An id new at every process start (Claude): the restart problem above.
- An id kept anywhere another machine could share (Claude): it would defeat the lock.

**Cost.** The lock identifies a volume, not a process, so two containers on one volume both hold it ([limitations](../limitations.md)).

**Source:** `src/aeca/adapters/copy_id_file.py`; [0039](0039-the-lock-is-a-courtesy.md).
