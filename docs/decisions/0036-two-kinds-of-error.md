# 0036: System-wide errors end the tick; email errors stay with the email

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- System-wide errors (a refused key, a 429, a 5xx, a dropped connection, the cost cap) end the tick. AECA backs off as a whole.
- An error specific to one email is recorded as `failed`, and the email gets `AECA/error`. The next polls retry it, up to 3 times. After that it keeps `AECA/error` and also gets `AECA/processed`.

**Why.** No email is charged for a system-wide error, so an outage of a few minutes fails nothing.

**Rejected.**
- Counting every error against the email in hand (inferred from the reason).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.9.
