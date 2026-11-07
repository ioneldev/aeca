# Saturday, 2026-11-07

The build ran on Saturday. This is what was built and run, in order, with the records of the runs.

## Built

- **The foundation:** ports, the domain models, the state store, the audit log and the trace, the decision with one validated model call, the policy records and reply rules, the Gmail and Calendar adapters, and the meeting planner.
- **CI** (GitHub Actions): ruff, strict mypy, the offline suite, the image build, a container test that no key sits in an image layer, and a dependency audit.
- **Docker and `make`:** the image, preflight, `make auth`, `make start`, `make stop`, `make logs`, `make audit`, `make approvals`.
- **The corpus, `make eval`, `make seed-calendar` and `make rehearse`.** The rehearsal sends through Mail.app ([0057](../decisions/0057-rehearsal-through-mail-app.md)).
- **Execute-once:** the journal, drafts, the thread check and derived ids ([0031](../decisions/0031-every-action-goes-through-one-journal.md)).
- **Approvals by email,** the approval loop, the re-check on APPROVE and supersede ([architecture](../architecture.md#the-approval-loop)).
- **Startup checks, the cost cap and incident alerts.**
- **A vague meeting request gets alternatives** ([0058](../decisions/0058-vague-requests-get-alternatives.md)).

## Run

| Run | What | Result |
|---|---|---|
| [0001](../decisions/0001-day1-spikes.md) | The day-1 spikes against the real account | Scopes, threads, headers, event ids, drafts and DMARC read as the design assumed. Items 12, 13, 15 and 17 not settled. |
| [0002](../decisions/0002-first-live-run.md) | The first live run, in Docker | 5 emails, $0.087, one audit line each, a graceful stop |
| [0003](../decisions/0003-first-send-rehearsal.md) | The first `send` rehearsal | 27 of 27 processed in about 3 minutes for $0.16; 26 of 27 matched the corpus |

## Decisions made on the day

- Rehearse through Mail.app, with no new accounts (Ionel): [0057](../decisions/0057-rehearsal-through-mail-app.md).
- A vague request gets alternatives (Ionel): [0058](../decisions/0058-vague-requests-get-alternatives.md).
- The consent screen is "In production" (Ionel). The token doesn't expire after 7 days.

## What the day taught

- The rehearsal's one personal outside address made the model call a phishing-like invoice SPAM, which `make eval` doesn't. Eight corpus emails can't be rehearsed live.
- Holds work: a second requester proposing the same slot was offered alternatives, as designed.
- A model call costs about half a cent warm and about 2 cents after five quiet minutes, when the prompt cache expires.
