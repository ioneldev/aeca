# AECA

AECA, the Agentic Email & Calendar Assistant, watches one Gmail inbox. It gives each new email one label, takes the action a policy names for that label, and asks the manager by email before any action that needs approval. It writes one audit line per email.

It is Ionel Cîmpan's take-home for Telekom's Senior Developer Python LLM role. [DESIGN.md](DESIGN.md) is the short version of why it works the way it does. [docs/](docs/architecture.md) holds the rest.

## What it does

| Label | What AECA does | Approval |
|---|---|---|
| `MEETING_REQUEST` | A proposed time is free: creates the event, then confirms to the requester. None free, none proposed, or the window is vague: drafts a reply with up to 3 free slots. The invitation is already on the calendar: the label only. | Yes |
| `TASK` | The label; may acknowledge | No |
| `INFO_ONLY` | An invoice or receipt gets the `Finance` label and is archived. Anything else: the label; may thank. | No |
| `SALES_OUTREACH` | A polite decline, then archives | No, unless `SALES_DECLINE_NEEDS_APPROVAL=true` |
| `MARKETING` | Archives; may acknowledge | No |
| `SPAM` | Moves to Gmail's Spam folder, never replies | No |
| `UNKNOWN` | The label only. The email couldn't be classified. | No |
| `URGENT` (added to any of the above) | Stars it. From an internal sender, the approval request carries the urgent notice and a reply drafted to the sender; for a meeting request, the meeting reply is the only reply ([0059](docs/decisions/0059-an-urgent-meeting-gets-one-reply.md)). A separate notice goes to the manager only when no approval carries it. | The drafted reply waits for approval |

Every email also gets `AECA/processed` when AECA is done with it, `AECA/awaiting` while an approval is open, and `AECA/error` if it failed.

Code owns the policy. The model labels the email and extracts facts, such as the proposed times. It holds no tool that changes anything, and it writes one reply only: the urgent draft, which the manager reads first. The acknowledgements, thanks, declines and confirmations are templates. Why: [decision 0022](docs/decisions/0022-the-model-writes-only-the-urgent-draft.md).

## Set up

You need Docker with the Compose plugin, `make`, and an Anthropic API key. Ionel sends you four values for the AECA inbox: `GMAIL_ADDRESS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REFRESH_TOKEN`. On Windows, `make` needs WSL. The image builds on arm64 and amd64.

```sh
cp .env.example .env     # put Ionel's four values in it, and your own ANTHROPIC_API_KEY
make preflight           # checks Docker, Compose, the daemon and .env
make image               # builds the image locally; nothing is published
make start               # runs AECA in the background
```

`make auth` is not part of a normal start. Run it only when the token Ionel sent stops working, or to install AECA on another account. It prints a link; open it, pick the account and consent. The new token is written into `.env` without being printed.

If your own consent screen is in Testing mode, tokens last 7 days. Run `make auth` again when one expires.

| Command | What it does |
|---|---|
| `make preflight` | Checks `docker`, Compose, a running daemon, `.env`, and that each required setting has a value. It never prints a value. |
| `make image` | Builds the one image that both Compose services use. |
| `make auth` | Google consent in the browser, for a new token. Not needed with the token Ionel sent. |
| `make start` | Starts AECA in the background. It restarts itself unless you stop it. |
| `make stop` | Stops AECA after the email in hand, within 2 minutes. |
| `make logs` | Follows one JSON summary line per email. |
| `make audit` | Prints the audit log. It works whether or not AECA is running. |
| `make approvals` | Lists the approvals the manager hasn't answered. |
| `make check` | Lint, strict typing and the offline tests. No network, no cost. |

### Health and the copy lock

**Health.** `aeca health` exits 0 when AECA's last successful poll is less than 3 × `POLL_SECONDS` old, and exits 1 with one line saying why otherwise. It reads only a heartbeat file in the data volume, so it needs no credential and no network. The image's Docker `HEALTHCHECK` runs it every 30 seconds, with a 60-second start period. `docker compose ps` shows the result.

**Stopping.** `make stop` sends SIGTERM. AECA finishes the email in hand, then exits. Compose waits up to 120 seconds.

**The copy lock.** Two copies on one inbox would do the work twice, so a running copy holds a lock in the Google account itself: a Gmail draft titled `AECA lock`. AECA never sends it.
- The copy renews the lock's heartbeat on every poll, and between emails when a poll runs long.
- A heartbeat is stale after 3 × `POLL_SECONDS`. A copy that starts while another copy's heartbeat is fresh exits with a configuration error and sends nothing. Wait out the stale time, stop the other copy, or set `FORCE_LOCK=true`.
- The copy's id is kept in the data volume (`copy-id`), so a restart of the same installation takes its own lock back at once. Another machine has another volume, so another id. A wiped volume is a new copy, which waits out the stale time.
- If several lock drafts exist, the one with the lowest draft id is the lock.
- A copy that finds it no longer holds the lock stops with exit 1. Docker restarts it, and it exits with a configuration error for as long as the other copy holds the lock.
- The lock is a courtesy. The ids AECA derives from Gmail message ids are what stop duplicate work ([0039](docs/decisions/0039-the-lock-is-a-courtesy.md)).

Limits of the lock are in [docs/limitations.md](docs/limitations.md).

### `.env`

Each setting is an environment variable. A blank value means the default.

| Variable | Default | What |
|---|---|---|
| `GMAIL_ADDRESS` | required | The AECA inbox. Ionel sends it. It must be the account the token belongs to, ignoring case and Gmail's dots. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | required | The OAuth client. Ionel sends them. |
| `GOOGLE_REFRESH_TOKEN` | required to run | The token. Ionel sends it. `make auth` writes a new one if it stops working. |
| `ANTHROPIC_API_KEY` | required to run | The model key. You bring your own. |
| `MANAGER_ADDRESS` | the AECA account | Where approval requests, urgent notices and alerts go, and the one outside address whose replies can approve. |
| `INTERNAL_SENDERS` | empty | Comma-separated addresses that count as internal. Empty makes every urgent sender count as internal. |
| `BULK_SENDERS` | empty | Addresses or domains that are always bulk. They never get an automatic reply. |
| `TIMEZONE` | the calendar's zone | Overrides the calendar's time zone. |
| `WORK_HOURS` | `Mon-Fri 09:00-17:00` | Where alternative slots are found. |
| `MEETING_MINUTES` | 30 | A meeting's length when the email doesn't say. |
| `BUFFER_MINUTES` | 10 | The gap kept around events. |
| `ALTERNATIVE_DAYS` | 5 | How many working days ahead to look for alternatives. |
| `POLL_SECONDS` | 30 | How often the inbox is read. |
| `SALES_DECLINE_NEEDS_APPROVAL` | false | Makes the sales decline wait for approval. |
| `MAX_USD_PER_DAY` | 5 | The daily cost cap. |
| `ALERT_AFTER_FAILURES` | 3 | How many emails in a row must fail before AECA alerts the manager. |
| `DEBUG_LOG` | false | Switches the debug log on. It holds full email text, which is personal data, so it is off. |
| `DEBUG_LOG_DAYS` | 7 | How many days of debug log files are kept. |
| `EXTRA_CA_CERTS` | empty | A path inside the container to a CA file to trust. See below. |
| `FORCE_LOCK` | false | Takes the copy lock even when another copy's heartbeat is fresh. Use it only after stopping the other copy. |
| `REHEARSAL_INTERNAL_SENDER`, `REHEARSAL_EXTERNAL_SENDER` | empty | Only for `make rehearse` (Ionel's rehearsal, not yours). |

The secrets reach the container only at run time, through Compose's `env_file`. They are in no image layer, and a test checks that.

### Behind a proxy that intercepts TLS

`HTTPS_PROXY`, when set on your machine, is passed to the container. If the proxy re-signs TLS, AECA fails at startup naming the proxy and `EXTRA_CA_CERTS`. `EXTRA_CA_CERTS` is a path inside the container, so mount the file. Create `compose.override.yaml` next to `compose.yaml`:

```yaml
services:
  aeca:
    volumes:
      - ./corporate-ca.pem:/certs/ca.pem:ro
```

Then set `EXTRA_CA_CERTS=/certs/ca.pem` in `.env` and run `make start`. Without the mount AECA exits with a configuration error, and Docker restarts it. The CA is added to the default roots, not used instead of them.

## Before you start

AECA needs no answers from you. Three settings are yours to choose, and what it assumes for the rest is below.

**`MANAGER_ADDRESS`: who approves.**
- Set it to your own address, and approve from your own mailbox. You never need to log in to the AECA account.
- Leave it empty, and approval requests go to the AECA account itself. You approve from its inbox. Google may challenge a login from a new device.
- A reply from `MANAGER_ADDRESS` counts only when Gmail's DMARC check passed for it, so its domain must publish DMARC. Gmail, Outlook and most companies do.

**`INTERNAL_SENDERS`: who is internal.** It only matters for urgent email: an urgent email from an internal sender gets the manager a notice and a drafted reply. Leave it empty and every urgent sender counts as internal, so you still see that scenario. AECA logs a warning at startup when it is empty.

**`TIMEZONE`.** AECA uses the calendar's own time zone, and states the zone in every approval and confirmation. Set `TIMEZONE` to override it.

**What AECA assumes for the rest:**
- **Where the 20 emails come from:** several mailboxes, one, or the AECA account itself. Labels, approvals and meetings work the same from any of them. One difference: AECA sends no automatic reply (an acknowledgement, a thank-you or a decline) to the account itself, so an email sent from the AECA account gets its label and no such reply ([DESIGN.md](DESIGN.md), defence 3). Send from another mailbox to see those replies.
- **How the calendar is filled** doesn't matter: events created directly and real invitations both work. An email whose invitation is already on the calendar gets its label and no action.
- **Language:** English only.
- **Attachments:** their names and types are read, not their contents.
- **Mail from one sender.** If you send all 20 from one mailbox, the never-Spam filter (below) should stop Gmail filing that sender's following mail in Spam after AECA moves a phishing email there. The first rehearsal didn't test this ([0003](docs/decisions/0003-first-send-rehearsal.md)).
- **One email at a time,** oldest first. 27 emails took about 3 minutes in the rehearsal.
- **Cost:** about half a cent per email with a warm cache, about 2 cents after a quiet spell. The cap is `MAX_USD_PER_DAY`.

## How to approve

An approval request arrives at `MANAGER_ADDRESS` as a new email, in a thread of its own, with the subject `[AECA] Approve: <action>`. It says who wrote the original email, what AECA would do, in which time zone, and the draft, if the action is a reply.

Reply in that thread. Write `APPROVE` or `REJECT` as the first word, above any quoted text.

- **One request covers every action it lists.** For a meeting that is the event and then its confirmation. The first answer word decides for all of them.
- **Reading the reply.** AECA reads the first unquoted line that starts with APPROVE, APPROVED, REJECT or REJECTED. A signature or disclaimer is ignored. A reply with two different answer words is unclear, and gets one help reply.
- **Who can approve.** A reply written in the AECA inbox, or a reply from `MANAGER_ADDRESS` whose single, well-formed `From` passed Gmail's DMARC check for its own domain. Anything else is ignored and audited with the reason. A forwarding address or mailing list that rewrites the `From` can't approve.
- **REJECT** is logged, and nothing else happens. The email keeps its label.
- **No model reads a reply.** Code does.
- Approvals don't expire. The slots they hold do. If a slot fills before the answer, AECA supersedes the request and sends a new one with the next free slots.

`make approvals` lists the requests still waiting.

## Where the audit is

`make audit` prints `audit.jsonl`, one line per processed email and one per approval outcome. A line holds the time, message and thread ids, sender and subject (bounded), the label, whether it is urgent, what decided it (a rule or the model), the actions with their status, the approval and its state, the outcome (`done`, `awaiting_approval` or `failed`), and the tokens and cost. It never holds a body.

`make logs` follows the same emails as one JSON line each. The files live in the `aeca-data` volume: `audit.jsonl`, `trace.jsonl`, and the debug log (below). The trace holds each model call with its tokens, cost and latency; model retries, corrections and refusals; suspected injections, by length only; each approval request; each email resumed; and each failure of an action, an email, an approval, an alert or a resume. It does not trace Google calls one by one ([limitations](docs/limitations.md)). API keys and tokens are redacted before anything is written.

**The debug log.** With `DEBUG_LOG=true`, AECA writes `debug-YYYY-MM-DD.log` (the UTC day) in the same volume. It is off by default because it holds personal data: secrets are redacted, email content is not. For each email the model decides, it holds the email text exactly as the model sees it (headed `email <message id> as the model sees it:`), each raw model reply, usable or not (headed `reply to <message id>, attempt <n>:`), and the text of any instruction the model reports as a suspected injection (headed `suspected injection in <message id>:`; the trace records only its length). An email that a rule decides alone writes nothing. Each file rotates at 10 MB, keeping 5, and files older than `DEBUG_LOG_DAYS` days are deleted when a new day's file is opened. Read one with `docker compose exec aeca sh -c 'tail -n 50 /data/debug-*.log'`.

## What AECA leaves alone

- **Gmail's Spam folder.** AECA reads the Inbox only. Mail that Gmail itself files in Spam gets no audit line. AECA does move phishing there itself.
- **The never-Spam filter.** Without it, Gmail could file a phishing test email in Spam before AECA sees it. On a new account, create a Gmail filter that matches every message and is set to "Never send it to Spam". Ionel sets it up on the handover account, and checks it before the handover. It can't be made through the API without another OAuth scope ([decision 0033](docs/decisions/0033-spam-folder-left-alone-with-a-never-spam-filter.md)).
- **Its own mail and approval threads.** AECA doesn't classify its own messages, or the threads of its approval requests.
- **Threads.** Every change applies to one message, never to a thread, so archiving one email can't archive a neighbour AECA hasn't read yet.

## Where AECA departs from the brief

Each has a decision record. Ionel decided all but the last two.

- **`UNKNOWN` is a seventh label.** A failed classification must not be guessed as one of the six. [0010](docs/decisions/0010-unknown-and-finance-labels.md)
- **`Finance` is an added label,** for invoices and receipts. [0010](docs/decisions/0010-unknown-and-finance-labels.md)
- **The sales decline and the acknowledgements go out without approval.** They follow the brief's own approval column. This does contradict "manager approval via email reply before any send"; `SALES_DECLINE_NEEDS_APPROVAL` turns it on for the decline. [0011](docs/decisions/0011-approval-column-and-unattended-replies.md)
- **Mail that Gmail files in Spam gets no audit line.** [0033](docs/decisions/0033-spam-folder-left-alone-with-a-never-spam-filter.md)
- **The model is Anthropic, not Azure OpenAI,** so you bring an Anthropic key. [0042](docs/decisions/0042-anthropic-model-and-the-evaluators-key.md)
- **A vague meeting request gets alternatives.** A proposed window wider than 12 hours isn't a proposed time. [0058](docs/decisions/0058-vague-requests-get-alternatives.md)
- **No MCP server** (Claude). The brief makes it optional. [0043](docs/decisions/0043-english-only-and-no-mcp-server.md)
- **Every `MARKETING` email is archived** (Claude), known bulk sender or not, because the evaluators' emails will come from ordinary mailboxes. [0020](docs/decisions/0020-marketing-is-always-archived.md)

## Known limits

AECA sends the inbox's contents to Anthropic. It reads its secrets from `.env`. It polls. It serves one inbox per copy. A state volume wiped between an event and its confirmation leaves the confirmation unsent. The full list, with what each costs, is in [docs/limitations.md](docs/limitations.md).

## Tests and the rest of the docs

`make check` runs lint, strict typing and the offline suite. `make eval` and `make rehearse` use the network and cost money, so they stay out of `make check`. `make eval` decides every email of a 35-email corpus for real and reports accuracy per label: 100% on every label at the last run, for about $0.20. The runs are recorded in [0002](docs/decisions/0002-first-live-run.md) and [0003](docs/decisions/0003-first-send-rehearsal.md).

## Ionel's rehearsal tools

These are for Ionel's own checkout, not for you. `make rehearse` sends the corpus through Mail.app from the two `REHEARSAL_*` addresses. `make reset-test` cleans up after it.

`make reset-test` refuses when `REHEARSAL_INTERNAL_SENDER` or `REHEARSAL_EXTERNAL_SENDER` is empty, as the table above leaves them. Run as it comes, it prints a dry run and changes nothing. `make reset-test ARGS=--yes` applies the plan.
- **What it matches**, one message at a time, never a thread:
  - R1: a single `From` equal to a rehearsal address, with `dmarc=pass`.
  - R2: AECA's own mail, with every recipient a rehearsal address.
  - R3: AECA's own mail with an approval id derived from a rehearsal email.
  - R4: a hand reply whose `In-Reply-To` or `References` names a rehearsal approval request.
  - E1: events tagged by `make seed-calendar`, the whole series.
  - E2: events under ids AECA derived from a rehearsal email.
- **What it writes:** `messages.trash` and `events.delete`, which cancels an event. Never threads, drafts, labels or the lock.
- **When it refuses:** while any `AECA lock` draft has a heartbeat younger than the larger of 3 × `POLL_SECONDS` and 10 minutes, dated in the future, or unreadable, and when the drafts can't be listed. It ignores `FORCE_LOCK`. Run `make stop`, wait 10 minutes, and run it again.
- **Its output** lists each item with its rule, the totals per rule, and a "left alone" summary by sender: what the rules didn't match, and how much of it sits in the Inbox unprocessed. A draft that a rule matches is listed and never trashed. Check that summary before `--yes`.
- **Re-seeding:** `make seed-calendar` after a reset brings the cancelled seed events back, each with its own status.

Why these rules: [0061](docs/decisions/0061-what-reset-test-treats-as-the-rehearsals.md), [0062](docs/decisions/0062-reset-tests-own-lock-check.md), [0063](docs/decisions/0063-a-reseed-restores-cancelled-seed-events.md). The limits are in [docs/limitations.md](docs/limitations.md).

| Page | What |
|---|---|
| [DESIGN.md](DESIGN.md) | Two pages: the flow, the defences, the limits, future ideas |
| [AGENTS.md](AGENTS.md) | The entry point for anyone, human or agent, working on the repo |
| [docs/architecture.md](docs/architecture.md) | Layers, modules, the per-email flow, the approval loop, exactly-once |
| [docs/glossary.md](docs/glossary.md) | Terms |
| [docs/limitations.md](docs/limitations.md) | What AECA doesn't do, and what it costs |
| [docs/decisions/](docs/decisions/README.md) | One record per decision, with what was rejected |
| [docs/guides/saturday.md](docs/guides/saturday.md), [docs/guides/sunday.md](docs/guides/sunday.md) | What was built and run each day |
