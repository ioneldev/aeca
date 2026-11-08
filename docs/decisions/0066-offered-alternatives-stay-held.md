# 0066: Offered alternatives stay held until each slot starts

**Who decided:** Claude, after the final review (finding M5), 2026-11-08. It changes [0025](0025-holds-keep-two-requesters-apart.md) in part, and departs from one line of spec §4.4.

**Decision.**
- An `executed` approval whose actions include the reply offering alternatives keeps its holds. Each lapses when its slot starts, as every hold does.
- Every other approval releases its holds as before: on REJECT, when superseded, when failed, and once executed when it offered nothing.
- The holds are still keyed by the requester's thread, so that thread still sees them as free, and a reply of "the second slot works" still fits.

**Why.** Spec §4.4 defines a hold as "a slot in an approval nobody has answered yet, or one of the alternatives offered to a requester", so that "two requesters are never offered the same slot". The same section also lists "once it's executed" among the releases. For an alternatives approval the two lines disagree: executing it is the moment the slots are offered, so releasing them then left them free for the next requester. The final review's probe showed it: 3 slots offered, 3 held while the manager decided, 0 once the offer was sent. The definition, and the reason it gives, win.

**Rejected.**
- Releasing on execution, as before: a later meeting request could be offered the same three slots.
- Releasing the other offered slots once the requester picks one: a second release path, built and tested on handover day, for a gain of a few days.

**Cost.** Two of the three offered slots stay blocked for other requesters until they start, even after the requester picks one. Alternatives lie within `ALTERNATIVE_DAYS`, so that is at most a few days.

**Source:** `src/aeca/domain/approval.py` (`Approval.holds_its_slots`); `src/aeca/adapters/sqlite_state.py`; [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.4; the final review's finding M5.
