# 0018: A From whose DMARC check failed is never internal

**Who decided:** Claude. 2026-11-05.

**Decision.**
A `From` whose DMARC check failed never counts as internal, even when it is in `INTERNAL_SENDERS`.

**Why.** A forged internal address can't trigger the notice or the draft.

**Rejected.**
- Trusting the `From` header alone (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2.
