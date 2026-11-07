# Sunday, 2026-11-08

Failure handling, production readiness, the docs and the handover. Sunday ends with the handover in the evening.

## Built by the time of writing

- **Failure handling:** two kinds of error, startup checks that name the next step, the cost cap, and one alert per incident ([0036](../decisions/0036-two-kinds-of-error.md), [0038](../decisions/0038-production-ready-is-a-requirement.md)).
- **The docs:** README, DESIGN, AGENTS, this wiki and the decision records, with a test that every relative link resolves.

## Placeholders

<!-- Task 15 -->
- **Task 15:** the health check, the copy lock and `FORCE_LOCK`, and the debug log. The README, architecture and limitations mark where they go.

<!-- Task 17 -->
- **Task 17:** `make reset-test` and the full rehearsal with approvals ([0046](../decisions/0046-rehearse-with-real-mail-on-the-handover-account.md)). Each is filled in after the rehearsal, with its run record.

## The handover checklist

Ionel runs it literally ([spec §5](../superpowers/specs/2026-11-05-aeca-design.md#5-testing)):
1. Stop Ionel's copy.
2. `make reset-test`, and clear the volume with `docker volume rm aeca_aeca-data`.
3. Look at the Inbox, Sent and the calendar: nothing from the rehearsal is outside Trash.
4. Check the never-Spam filter ([0033](../decisions/0033-spam-folder-left-alone-with-a-never-spam-filter.md)), and that the token is valid.
5. Follow every README command exactly as written, on a fresh clone, then `make stop`.

Gmail empties Trash after 30 days, so the rehearsal stays visible there until then. The handover message says so.

## If the day slips

Cut in the order Ionel set ([0054](../decisions/0054-if-the-days-slip-cut-in-this-order.md)): the onboarding page, then CI, then the eval's confusion matrix. The approvals, failure handling, production readiness, the README and the docs are never cut.
