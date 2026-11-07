# Sunday, 2026-11-08

Failure handling, production readiness, the docs and the handover. Sunday ends with the handover in the evening.

## Built by the time of writing

- **Failure handling:** two kinds of error, startup checks that name the next step, the cost cap, and one alert per incident ([0036](../decisions/0036-two-kinds-of-error.md), [0038](../decisions/0038-production-ready-is-a-requirement.md)).
- **The docs:** README, DESIGN, AGENTS, this wiki and the decision records, with a test that every relative link resolves.

## Built since

- **Task 15:** the health check, graceful stop, the copy lock and `FORCE_LOCK` ([README](../../README.md#health-and-the-copy-lock), [0065](../decisions/0065-the-copy-id-is-persisted-in-the-data-dir.md)). The debug log's file handling is built; no step writes to it yet ([limitations](../limitations.md#the-debug-log)).
- **Task 17:** `make reset-test`, and `make seed-calendar` restoring cancelled seed events ([0061](../decisions/0061-what-reset-test-treats-as-the-rehearsals.md) to [0063](../decisions/0063-a-reseed-restores-cancelled-seed-events.md)).

## The plan for today

1. Ionel approves `make stop`.
2. The first live `make reset-test` of the Task 13 run, shown dry first, then applied with `ARGS=--yes`.
3. Clearing the state volume.
4. Then:
   - `make image`;
   - `make seed-calendar`, which restores the cancelled seed events;
   - `make start`;
   - `make rehearse`;
   - Ionel answers from Proton;
   - the run record 0004 <!-- filled after the run -->.
5. `make stop`, the final reset, and a second dry run that must be empty.
6. The handover checklist below, plus the closing dry run of [0064](../decisions/0064-the-handover-ends-with-a-reset-dry-run.md).

The full rehearsal comes after a reset because the rehearsal subjects carry no run marker. A second run would join the first run's threads, and the thread check would refuse replies.

Run record 0004's numbers: <!-- filled after the run -->

## If the day slips

Cut in the order Ionel set ([0054](../decisions/0054-if-the-days-slip-cut-in-this-order.md)): the onboarding page, then CI, then the eval's confusion matrix. The approvals, failure handling, production readiness, the README and the docs are never cut.
