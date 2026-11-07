# 0063: A re-seed restores cancelled seed events

**Who decided:** Claude, 2026-11-07.

**Decision.**
- `make seed-calendar` restores a cancelled seed event, writing the seed body over it with its own status. A tentative event stays tentative. A live event is skipped.
- Measured: once an event is deleted, `events.get` returns it as cancelled, and inserting the same id answers 409. `events.update` with a status brings it back.

**Why.** reset-test cancels the seeded events, and the full rehearsal after it needs the same week seeded again ([sunday guide](../guides/sunday.md)).

**Rejected.**
- Always restoring with `confirmed` (Claude): it would change the seeded data, such as the tentative vendor call.

**Cost.** None found beyond one extra read per cancelled seed event.

**Source:** `src/aeca/adapters/google_seed_calendar.py`, `src/aeca/tools/seed_calendar.py`.
