# 0024: The planner reads events, not free/busy

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- Code reads the events in the proposed windows with `events.list(singleEvents=true)`.
- Busy means every event not declined or cancelled, including all-day events and invitations not yet answered. Holds are busy too, except the thread's own.
- It adds `BUFFER_MINUTES` around events. The meeting lasts what the email says, or else `MEETING_MINUTES`.
- It picks the earliest free slot in the first window that has one, in the order the times were proposed.
- A proposed slot outside working hours is accepted if it's free, and the approval email says so. Alternatives stay inside `WORK_HOURS`.

**Why.** Free/busy ignores all-day events, which default to "free". The spec marks this as a claim to verify on day 1.

**Rejected.**
- Google's free/busy call. It would offer a slot on top of an all-day event.

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.4 and §11.
