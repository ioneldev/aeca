# 0015: An UNKNOWN email gets the label and nothing else

**Who decided:** Ionel for no approval; Claude for no notification. 2026-11-05.

**Decision.**
- When classification fails, the email gets the `UNKNOWN` label only.
- There is no approval, and the manager gets no notification.

**Why.** The spec gives no further reason.

**Rejected.**
- Notifying the manager of every `UNKNOWN` email (inferred).
- Guessing a label (see 0010).

**Cost.** The manager isn't told about an `UNKNOWN` email (inferred).

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2.
