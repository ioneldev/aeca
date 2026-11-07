# 0064: The handover ends with a reset dry run

**Who decided:** Claude, 2026-11-08. It departs from the order in [spec §5](../superpowers/specs/2026-11-05-aeca-design.md#5-testing), where the reset comes before the README run.

**Decision.**
- The checklist's README run (step 5) starts a fresh copy after the reset, and that copy polls the inbox and writes to it.
- So Ionel runs `make reset-test` again from their checkout once that copy's lock is stale. If the dry run shows anything, they apply it with `ARGS=--yes`.

**Why.** The README run is the last real use of the account. Left as spec §5 orders it, the reset would run before it, and the evaluators would find its leftovers.

**Rejected.**
- A plan hash on `--yes` (Claude): the second dry run answers the question the handover asks, whether anything is left, and `--yes` already reads the lock again before it writes ([0062](0062-reset-tests-own-lock-check.md)).

**Cost.** Ionel waits for the stale window after the README run's `make stop`, 10 minutes ([0062](0062-reset-tests-own-lock-check.md)).

**Source:** [the Sunday guide](../guides/sunday.md); [0046](0046-rehearse-with-real-mail-on-the-handover-account.md).
