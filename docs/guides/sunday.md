# Sunday, 2026-11-08

Failure handling, production readiness, the docs and the handover. Sunday ends with the handover in the evening.

## Built by the time of writing

- **Failure handling:** two kinds of error, startup checks that name the next step, the cost cap, and one alert per incident ([0036](../decisions/0036-two-kinds-of-error.md), [0038](../decisions/0038-production-ready-is-a-requirement.md)).
- **The docs:** README, DESIGN, AGENTS, this wiki and the decision records, with a test that every relative link resolves.

## Built since

- **Task 15:** the health check, graceful stop, the copy lock and `FORCE_LOCK` ([README](../../README.md#health-and-the-copy-lock), [0065](../decisions/0065-the-copy-id-is-persisted-in-the-data-dir.md)), and the debug log: each decided email as the model sees it, and each raw reply ([limitations](../limitations.md#the-debug-log)).
- **Task 17:** `make reset-test`, and `make seed-calendar` restoring cancelled seed events ([0061](../decisions/0061-what-reset-test-treats-as-the-rehearsals.md) to [0063](../decisions/0063-a-reseed-restores-cancelled-seed-events.md)).

## The plan for today

1. Ionel approves `make stop`.
2. The first live `make reset-test` of the Task 13 run, shown dry first, then applied with `ARGS=--yes`. It needs no wait after `make stop`: the copy running now was built before the copy lock existed, so it writes no lock draft.
3. Clearing the state volume.
4. Then:
   - `make image`;
   - `make seed-calendar`, which restores the cancelled seed events;
   - `make start`;
   - `make rehearse`;
   - Ionel answers from Proton;
   - the run record 0004 <!-- filled after the run -->.
5. `make stop`, the final reset, and a second dry run that must be empty. This copy was built from the current code, so it holds a lock: the reset waits at least the larger of 3 × `POLL_SECONDS` and 10 minutes after `make stop`, and refuses before that ([0062](../decisions/0062-reset-tests-own-lock-check.md)).
6. The handover checklist below, with the closing dry run of [0064](../decisions/0064-the-handover-ends-with-a-reset-dry-run.md) as its step 6.

The full rehearsal comes after a reset because the rehearsal subjects carry no run marker. A second run would join the first run's threads, and the thread check would refuse replies.

Run record 0004's numbers: <!-- filled after the run -->

## The handover checklist

Ionel runs it literally ([spec §5](../superpowers/specs/2026-11-05-aeca-design.md#5-testing)), with one change: [0064](../decisions/0064-the-handover-ends-with-a-reset-dry-run.md) adds step 6.
1. Stop Ionel's copy.
2. `make reset-test`, shown dry first and then applied, and clear the volume with `docker volume rm aeca_aeca-data`. After a copy built from the current code, the reset waits for the lock to go stale, at least the larger of 3 × `POLL_SECONDS` and 10 minutes after `make stop`.
3. Look at the Inbox, Sent and the calendar: nothing from the rehearsal is outside Trash.
4. Check the never-Spam filter ([0033](../decisions/0033-spam-folder-left-alone-with-a-never-spam-filter.md)), and that the token is valid.
5. Follow every README command exactly as written, on a fresh clone, then `make stop`.
6. Once the README run's copy has stopped and its lock is stale, run `make reset-test` again from Ionel's checkout. If the dry run shows anything, apply it with `ARGS=--yes`.

Gmail empties Trash after 30 days, so the rehearsal stays visible there until then. The handover message says so.

## If the day slips

Cut in the order Ionel set ([0054](../decisions/0054-if-the-days-slip-cut-in-this-order.md)): the onboarding page, then CI, then the eval's confusion matrix. The approvals, failure handling, production readiness, the README and the docs are never cut.
