# 0010: UNKNOWN and Finance join the brief's six labels

**Who decided:** Ionel. 2026-11-05.

**Decision.**
- Two labels are added to the brief's six: `Finance`, and `UNKNOWN` for an email that couldn't be classified.
- `UNKNOWN` is a seventh primary label. `Finance` goes on an invoice or a receipt, and the email is then archived.

**Why.** A failed classification must not be guessed as one of the six. The spec gives no further reason for `Finance`.

**Rejected.**
- Guessing the nearest of the six when classification fails. It loses to the reason above.
- Marking invoices `INFO_ONLY` and nothing more (inferred).

**Cost.** The brief says one of six labels, so the README explains why there are seven.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.1, §4.2 and §12.
