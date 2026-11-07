# 0055: Approvals don't expire, but their holds do

**Who decided:** Claude. 2026-11-05.

**Decision.**
- An approval stays open until the manager answers it, or it is superseded.
- Its holds expire: they are released when the slot starts, among other cases (see 0025).

**Why.** The spec gives no further reason.

**Rejected.**
- Expiring an approval after a set time (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.4 and §4.5.
