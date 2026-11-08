# 0059: An urgent meeting request gets one reply, the meeting reply

**Who decided:** Ionel found the problem, and Claude chose the fix (2026-11-07). It departs from the spec's rule that an urgent email's rows add up ([0016](0016-urgent-handling-depends-on-the-sender.md)).

**Decision.**
- For an urgent MEETING_REQUEST from an internal sender, the meeting reply is the only reply: the confirmation of the booked time, or the offer of alternatives.
- The urgent row still stars the email and adds `URGENT`, and the approval request still carries the urgent notice, with the quoted lines and the reason it looks urgent.
- The model leaves the urgent draft empty for a meeting. If it writes one anyway, the validator drops it, and the audit notes why.
- Every other urgent internal email keeps its drafted reply, as in [0016](0016-urgent-handling-depends-on-the-sender.md).

**Why.** In the rehearsal, Dana's urgent meeting request produced one approval with two replies to her: item 1 offered alternatives, and item 2 was a draft that accepted Monday at 14:00. Ionel asked which one they were approving. Two replies to one requester can contradict each other, and an approval covers every action it lists ([0012](0012-event-and-confirmation-share-one-approval.md)), so the manager can't approve one and reject the other.

**Rejected.**
- Keeping both replies, and letting the manager edit one (Claude). AECA has no edit step: the manager answers APPROVE or REJECT.
- Dropping the meeting reply and keeping the draft (Claude). The draft is free text written from one email, so it can name a time that isn't free. The meeting reply is built by code from the calendar.

**Cost.** The sender of an urgent meeting request gets no personal note beyond the meeting reply.

**Source:** commit `4bf7fb0`; `src/aeca/domain/policy.py` and `src/aeca/domain/validation.py`; the corpus entry `meeting-urgent-internal-escalation`.
