# 0001: The day-1 spikes, measured

- **Run on:** 2026-11-07, against the AECA account `aecatest74@gmail.com`, by Claude, with Ionel's
  steps from Proton and from the AECA inbox.
- **How:** `uv run --env-file .env python docs/research/spikes/day1.py <item>`. The script prints
  ids, labels and header names, never a token or a body. Everything it sends has a subject starting
  `[AECA spike]` and the header `X-AECA-Test: spike`; `day1.py cleanup` moved the leftovers to Trash.
- **Source:** [spec §11](../superpowers/specs/2026-11-05-aeca-design.md#11-to-verify-on-day-1).

Each result below is *measured* unless it says otherwise.

## Results

| Item | Question | Result | Command |
|---|---|---|---|
| 2 | Do the three scopes cover every call? | Yes. `gmail.modify`, `calendar.events` and `calendar.settings.readonly` were granted, and `users.getProfile`, `labels.list`, `messages.list`, `drafts.list`, `settings.get("timezone")` and `events.list` all worked. `calendars.get` and `freeBusy` need other scopes; AECA uses neither. `GOOGLE_SCOPES` is unchanged. | `day1.py scopes` |
| 3 | Is a user label named `SPAM` reserved? | Yes: `labels.create` answers 400 "Invalid label name". SPAM is Gmail's system label only, and AECA never creates a user `SPAM` label. | `day1.py spam-label` |
| 4 | Does a reply with `threadId`, `In-Reply-To` and `References` land in the sender's thread? | Yes, on both sides. AECA's reply joined the Gmail thread of Ionel's Proton email, and Ionel's answer to it from Proton joined the same Gmail thread. | `day1.py reply --sender <Proton address>` |
| 5 | Does mail the account sends to itself keep `SENT` and `INBOX`, and does a reply to it stay in the thread? | Yes. The self-sent request carried `SENT` and `INBOX`. Ionel's `APPROVE` written in the AECA inbox joined the request's thread with `SENT` and `INBOX`, and no AECA headers. | `day1.py request --to aecatest74@gmail.com`, then `day1.py thread --thread <id>` |
| 6 | Which inbox query excludes `AECA/processed`? | `in:inbox -label:aeca-processed` excludes a message labelled `AECA/processed`, and so do `-label:AECA/processed` and `-label:aeca/processed`. `messages.list(labelIds=["INBOX"])` still lists it. | `day1.py query` |
| 7 | Do `X-AECA-*` headers survive `drafts.send`, and is a sent draft gone? | Yes. `X-AECA-Kind`, `X-AECA-Id` and `X-AECA-Test` survive, `messages.get` and `threads.get(format="metadata")` return them, and `drafts.get` on the sent draft answers 404. | `day1.py self-send` |
| 9 | Are all-day events returned, and can AECA read the zone? | `settings.get("timezone")` gives `Europe/Bucharest`. An all-day event created through the API comes back from `events.list(singleEvents=True)` with `start.date` and no `transparency` (opaque). | `day1.py calendar` |
| 10 | How does `events.get` behave on a client-chosen id? | Never created: 404. Live: 200, `confirmed`. After delete: 200, `status: cancelled`. Insert with a live id: 409 `duplicate`. Insert with a cancelled id: 409 `duplicate`. Deleting a cancelled event again: 410. Google refuses duplicate ids. | `day1.py event-ids` |
| 14 | Can a draft be updated in place? | Yes. `drafts.update` keeps the draft id (the message id inside changes), and the new body reads back. | `day1.py draft-update` |
| 16 | Does an outside reply land in the request's thread, and how does its authentication read? | Yes. Ionel's `APPROVE` from Proton joined the request's thread. Gmail's own header read `mx.google.com; dkim=pass header.i=@protonmail.com …; spf=pass … smtp.mailfrom=…@protonmail.com; dmarc=pass (p=QUARANTINE sp=QUARANTINE dis=NONE) header.from=protonmail.com`. | `day1.py request --to <Proton address>`, then `day1.py thread --thread <id>` |

**Not run on day 1.** Item 12 (matching an invitation by its iCalUID) and items 13 and 17 (Spam
filing, and the never-Spam filter) move to the first `send` rehearsal. Item 15 (a login from a clean
browser over a VPN) was not run. Item 8 belongs to `insert` mode, which was cut. Item 11 (Anthropic)
was checked by the opt-in smoke test `tests/adapters/test_anthropic_live.py`: structured output,
the default refusal fallback and a cached system prompt all work on `claude-opus-5-5`.

## Unplanned findings

- **AECA's own sent mail can carry `INBOX`.** The reply AECA sent to Proton and the request it sent to
  Proton both came back with `SENT` and `INBOX` once their conversation held inbox mail. The spec's
  §4.6 assumed mail sent to other people never has `INBOX`. AECA still never processes its own mail,
  because it recognises it by its `X-AECA-*` headers together with `SENT`.
- **`messages.list`'s `resultSizeEstimate` is unreliable.** It said 201 for an inbox of 4 messages.
  AECA never relies on it.

## What changed because of these results

- The Gmail adapter uses the query `in:inbox -label:aeca-processed` (item 6).
- SPAM is handled with the system `SPAM` label only (item 3).
- Creating an event treats a 409 `duplicate` as "this id already exists", and reads it back: live
  means done, cancelled means the next attempt (item 10).
- A reply from the manager's address counts only with Gmail's `dmarc=pass` (item 16); a reply from
  the AECA inbox counts by its `SENT` label (item 5).
