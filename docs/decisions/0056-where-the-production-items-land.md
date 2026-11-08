# 0056: Where the production items land in the four days

**Who decided:** Claude. 2026-11-05.

**Decision.**
- CI and JSON logs on day 1.
- The copy lock, the health check and the graceful shutdown on day 3, with the loop's hardening.
- The alerts, log rotation, debug-log retention and migrations on day 4.

**Why.** CI and JSON logs cost least at the start. The spec gives no further reason for the others.

**Rejected.**
- Leaving all production items to the last day (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.12 and §7.
