# 0058: A vague meeting request gets alternatives, not a booked time

**Who decided:** Ionel. 2026-11-07.

**Decision.**
- A proposed window wider than 12 hours is not a proposed time. A whole day, a week, or "sometime next week" all count as too wide.
- The validator drops such a window. A meeting left with no valid window takes the alternatives path ([0013](0013-no-free-slot-drafts-alternatives.md)): the manager approves a reply that offers up to three free slots.
- The width is compared in UTC, so a daylight-saving change can't make a window look wider or narrower.

**Why.** In the first rehearsal, "sometime next week" got an event at Monday 13:10, for approval. The model had read "next week" as a window, and the planner's rule, the earliest free slot in the first window that has one, booked the first free half hour. Nobody had asked for 13:10. Ionel judged that a booked time the requester never proposed is worse than three offered ones ([0003](0003-first-send-rehearsal.md), where it was marked `[NEEDS CLARIFICATION]`).

**Rejected.**
- Keeping the spec's rule for every window. It books a time nobody proposed.
- A width limit of a different size (Claude, inferred). 12 hours lets "Tuesday afternoon" and "Tuesday 9 to 5" through and stops "all day Tuesday" going on to a booking. `[NEEDS CLARIFICATION: 12 hours is a guess. Ionel can tune it after seeing real emails.]`

**Cost.** A requester who writes "any time Tuesday" gets an offer of three slots, not a confirmation. That is one more round trip, and it is the point.

**Source:** commits `cb47490` and `37a72d6`; `MAX_WINDOW_WIDTH` in `src/aeca/domain/validation.py`; [0003](0003-first-send-rehearsal.md).
