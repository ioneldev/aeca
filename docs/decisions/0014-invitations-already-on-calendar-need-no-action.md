# 0014: A real invitation already on the calendar needs no action

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- Google's setting that adds invitations to the calendar automatically stays on.
- An email whose invitation is already on the calendar is labelled `MEETING_REQUEST` and gets no action. The audit line says so.

**Why.** The evaluators may fill the calendar by sending invitations. Turning the setting off would keep their events off it.

**Rejected.**
- Turning the setting off, so AECA would create every event itself. It loses to the reason above.

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.4.
