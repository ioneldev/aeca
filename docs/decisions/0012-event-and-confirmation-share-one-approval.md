# 0012: One approval covers the event and its confirmation; the event has no attendees

**Who decided:** Ionel for the single approval, the confirmation and the missing attendees; Claude for the template text. 2026-11-05.

**Decision.**
- A meeting with a free proposed time creates the event, then sends the requester a confirmation. One approval covers both.
- The confirmation is a short reply in the requester's thread. Code fills its text from a template, the slot and the calendar's time zone.
- If creating the event fails, no confirmation goes out.
- The event has no attendees, so Google sends no invitation.

**Why.** The spec gives no further reason for the single approval or for dropping attendees. The template is Claude's call and fits the rule that no model-written text leaves without a human seeing it (see 0022).

**Rejected.**
- Two approvals, one per action (inferred).
- Adding the requester as an attendee, so Google would send an invitation (inferred).
- A model-written confirmation (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.4.
