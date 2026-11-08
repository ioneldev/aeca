# 0002: The first live run, in Docker

- **Run on:** 2026-11-07, 14:34–15:00 UTC, against `aecatest74@gmail.com`, by Claude, with one email
  from Ionel.
- **Build:** `build` at `14e2706`, Task 8's thin path. It labels an email, writes the audit line, then
  adds `AECA/processed`. Approvals and replies come in later tasks.
- **Model:** `claude-opus-5-5` at effort `low`.

## Steps

1. Before the run, `docs/research/spikes/day1.py cleanup` moved the day-1 spike threads to Trash.
   That left four Google account emails in the Inbox.
2. `make image && make start`.
3. AECA started (`service_started`, `poll_seconds: 30`). In its first tick, about 7 seconds per
   email, it labelled the four Google emails.
4. Ionel sent one email from Proton (subject "Q3 report", asking for a review by Friday).
5. AECA picked it up on the next poll.
6. `make audit` showed one line per email, and `make stop` ended the run.

## Results (measured)

| Email | Label | Decided by | Tokens in / out | Cost |
|---|---|---|---|---|
| Information about your new Google Account | INFO_ONLY | model | 11,334 / 90 | $0.050311 |
| Security alert | INFO_ONLY | model | 3,741 / 115 | $0.005199 |
| Security alert | INFO_ONLY | model | 3,838 / 100 | $0.005287 |
| Security alert | INFO_ONLY | model | 4,286 / 121 | $0.007499 |
| Q3 report (Ionel, from Proton) | TASK | model | 3,438 / 99 | $0.018907 |

- **Total: $0.087203**, for 26,637 tokens in and 525 out.
- **In Gmail,** the Q3 email carries `TASK` and `AECA/processed`, and stays in the Inbox.
- **On stdout,** `make logs` showed one JSON summary line per email.
- **In the audit,** `make audit` showed one audit line per email, with the label, `decided_by`, the
  action and its status, tokens and cost. There were no email bodies.
- **The stop was graceful.** `make stop` gave `service_stopped`, and the container exited with code 0.

## What the numbers say

- The first call pays to write the cached system prompt, and the welcome email is the longest of the
  five. Calls within a few minutes of each other read the cache and cost about half a cent each.
- The Q3 email came about 25 minutes after the last call. The 5-minute cache had expired, so that
  call paid to write the cache again ($0.019). Busy periods cost about $0.005 per email, and an
  email after a quiet spell about $0.02. The default cap of $5 a day covers several hundred emails.
- The evaluators' burst of 20 emails should cost about $0.15 for classification.

## Follow-ups

- The thin path is replaced by the full plan of actions in Task 11.
- The state layout is frozen at schema version 1 as of this run. Any later column change bumps the
  version and ships a migration.
