# 0054: If the days slip, cut in a fixed order

**Who decided:** Ionel for the order and the four days; Claude for putting production readiness on the never-cut list. 2026-11-05.

**Decision.**
- Cut in this order: `insert` mode, the scorer, the daily onboarding updates.
- Never cut: the approvals, failure handling, production readiness, the README and the handover.

**Why.** Production readiness is on the never-cut list because Ionel made it a requirement. The spec gives no further reason for the order.

**Rejected.**
- Cutting the approvals, failure handling, the README or the handover (inferred).

**Cost.** The cuts matter later: 0048 and 0050 record two of them as applied.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §7.
