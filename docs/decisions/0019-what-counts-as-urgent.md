# 0019: What counts as urgent

**Who decided:** Claude; Ionel can overturn it. 2026-11-05.

**Decision.**
- The brief says "deadline or escalation". AECA reads that from the manager's side: the manager must act soon, or something real is lost.
- Urgent: a deadline today or within two working days, an outage or incident, or an escalation.
- Not urgent: urgency on the sender's side ("Sale ends tonight!"), a deadline weeks away, or "URGENT" in a subject that nothing in the body supports.
- The model quotes the text that makes the email urgent, and the validator checks the quote.
- The `Importance` and `X-Priority` headers reach the model as a hint only.

**Why.** The spec reads "deadline or escalation" from the manager's side. It keeps the priority headers as a hint only, because marketers set them too.

**Rejected.**
- Letting the priority headers decide. Marketers set them too.
- Urgency on the sender's side, such as "Sale ends tonight!" (the spec lists it as not urgent).
- Reading "URGENT" in a subject as enough (the spec lists it as not urgent without body support).

**Cost.** The two-working-day window is a guess, tuned with `make eval`.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §4.7.
