# 0019: What counts as urgent

**Who decided:** Claude; Ionel can overturn it. 2026-11-05.

**Decision.**
- The brief says "deadline or escalation". AECA reads that from the manager's side: the manager must act soon, or something real is lost.
- Urgent: a deadline today or within two working days, an outage or incident, or an escalation.
- Not urgent: urgency on the sender's side ("Sale ends tonight!"), a deadline weeks away, or "URGENT" in a subject that nothing in the body supports.
- The model quotes the text that makes the email urgent, and the validator checks the quote.
- The `Importance` and `X-Priority` headers reach the model as a hint only.

**Why.** The brief's wording is short, and the manager's side is the one that matters. Marketers set priority headers too, so they can't decide.

**Rejected.**
- Taking urgency from the sender's wording or from priority headers. Both are easy to fake.
- Reading "URGENT" in a subject as enough (the spec lists it as not urgent without body support).

**Cost.** The two-working-day window is a guess, tuned with `make eval`.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.7.
