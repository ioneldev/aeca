# 0040: The schema carries a version, and an upgrade never drops an approval

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- The SQLite schema carries a version (`PRAGMA user_version`), checked at startup.
- A schema change ships with its migration. An upgrade never drops a pending approval.
- Version 1 needs no migration framework.

**Why.** The spec gives no further reason.

**Rejected.**
- A migration framework in version 1.

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.12.
