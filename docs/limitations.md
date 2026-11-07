# Limitations

What AECA doesn't do, and what each costs. The first four are named in the spec and are Ionel's decision ([0041](decisions/0041-limits-named-not-built.md)). The rest were found during the build.

## Named, and left as they are

- **The inbox goes to Anthropic.** In production that needs a data-processing agreement and a decision about where the data is processed.
- **Secrets come from `.env`,** not from a secret manager.
- **AECA polls.** At scale it would use Gmail push notifications and the history API.
- **One inbox per copy.**

## Scope

- **English only** ([0043](decisions/0043-english-only-and-no-mcp-server.md)). **No MCP server.**
- **Gmail's Spam folder isn't read** ([0033](decisions/0033-spam-folder-left-alone-with-a-never-spam-filter.md)). Mail Gmail files there gets no audit line. The never-Spam filter should leave only mail Gmail judges dangerous.
- **Attachments' contents aren't read,** only names and types.
- **No web UI, and no editing a draft by replying.**
- **One email at a time.** 27 emails took about 3 minutes. A burst of hundreds would take hours.

## Exactly once has limits

- **A state volume wiped after an event is created and before its confirmation is sent leaves the confirmation unsent.** It is at most once there, not exactly once. The event exists; the requester never hears.
- **After a wiped volume, a message in a thread of its own** (an approval request, an urgent notice, an alert) is found again only among the 25 most recent sent messages. An older one could be sent again.
- **Two copies starting in the same moment can both take the lock** (Gmail drafts have no compare-and-swap), and then each may send an approval request. The manager would see two; the second APPROVE finds the event already created ([0039](decisions/0039-the-lock-is-a-courtesy.md)). <!-- Task 15 -->

## Approvals

- **A forwarding address or a mailing list that rewrites the From or breaks DMARC can't approve.** Reply from the AECA inbox instead.
- **Approval is all-or-nothing.** One request covers every action it lists. An inline "no" under one step isn't read as rejecting that step.
- **REJECT only logs** ([0030](decisions/0030-reject-does-nothing.md)).
- **Approvals don't expire,** so a forgotten one stays open ([0055](decisions/0055-approvals-do-not-expire-but-holds-do.md)).

## Gmail

- **AECA's own sent messages carry `INBOX`** once a conversation has inbox mail. AECA recognises them by its headers plus `SENT`, not by a missing label.
- **A failing Gmail can't alert.** When Gmail itself fails, for example a revoked token, no mail can go out. The signals left are the health check, the logs and a non-zero exit. A key refused at startup sends no mail either: AECA exits with the next action, and Docker restarts it.
- **Alerts are one per incident kind per UTC day.** A second incident of the same kind that day sends no mail.
- **Consent screen in Testing mode:** tokens last 7 days. In production they don't.

## The model

- **Labels are a judgement.** The corpus score is 100% on 35 emails the build was tuned on. The evaluators' 20 are new.
- **The 12-hour limit on a meeting window is a guess** ([0058](decisions/0058-vague-requests-get-alternatives.md)).
- **The urgent window of two working days is a guess, tuned with `make eval`** (spec §4.2, [0019](decisions/0019-what-counts-as-urgent.md)).
- **Cost.** About half a cent per call with a warm cache, about 2 cents after a quiet spell. The cap is `MAX_USD_PER_DAY` ([0002](decisions/0002-first-live-run.md)).

## Testing

- **The rehearsal sends 27 of the 32 deliverable corpus emails.** Mail.app can set neither custom headers nor HTML. The other 5, and the 3 a real account can't send, run only in `make eval` ([0057](decisions/0057-rehearsal-through-mail-app.md)).
- **Spikes 12, 13, 15 and 17 of [spec §11](superpowers/specs/2026-11-05-aeca-design.md#11-to-verify-on-day-1) weren't settled** by the runs so far ([0001](decisions/0001-day1-spikes.md), [0003](decisions/0003-first-send-rehearsal.md)).
