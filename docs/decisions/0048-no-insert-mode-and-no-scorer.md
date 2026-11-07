# 0048: No insert mode and no scorer

**Who decided:** Ionel. 2026-11-07.

**Decision.**
- There is no `insert` mode and no scorer.
- The cases that need a forged sender run only in `make eval` and the offline suite.
- Results are checked by eye.

**Why.** The spec already put these first and second on the cut list. The plan gives no further reason.

**Rejected.**
- Building them, as the spec first planned (see 0046).

**Cost.** Forged-sender cases never meet real Gmail. There is no pass or fail per email; results are read by eye with `make audit`.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md) and [the plan's Deviations](../superpowers/plans/2026-11-07-aeca.md), §5 and §7.
