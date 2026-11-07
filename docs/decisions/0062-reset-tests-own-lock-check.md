# 0062: reset-test has its own lock check

**Who decided:** Claude, 2026-11-07.

**Decision.**
- reset-test refuses while any lock draft could be live: younger than the larger of 3 × `POLL_SECONDS` and 10 minutes, dated in the future, or unreadable. It also refuses when listing the drafts fails.
- It ignores `FORCE_LOCK`, reads the lock again before the first write, and never writes the lock.

**Why.** The lease's startup rules fail open: an unreadable lock counts as free, `FORCE_LOCK` takes it, and a heartbeat far in the future counts as stale. That is right for a copy starting up and wrong for a tool that trashes mail. The lock doesn't record the holder's `POLL_SECONDS`, so a copy that polls rarely could look stale to this checkout's setting. The 10-minute floor covers that.

**Rejected.**
- Reusing the lease's acquisition rule (Claude): it fails open in the three ways above.

**Cost.** Ionel waits 10 minutes after `make stop` before reset-test will run.

**Source:** `src/aeca/domain/reset_lock.py`, `src/aeca/adapters/google_reset.py`; [0039](0039-the-lock-is-a-courtesy.md).
