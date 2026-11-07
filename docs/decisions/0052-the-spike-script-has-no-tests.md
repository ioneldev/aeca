# 0052: The spike script has no tests

**Who decided:** [NEEDS CLARIFICATION: the plan marks this neither Ionel's nor Claude's]. 2026-11-07.

**Decision.**
The spike script in `docs/research/spikes/` has no tests and is excluded from ruff and mypy.

**Why.** It is throwaway evidence, as in ARDE.

**Rejected.**
- Testing and linting it like product code (inferred).

**Cost.** It departs from Ionel's convention of tests for new logic.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md) and [the plan's Deviations](../superpowers/plans/2026-11-07-aeca.md), §11.
