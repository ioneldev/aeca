# AECA design: the Agentic Email & Calendar Assistant

- **Date:** 2026-11-05
- **Status:** draft. The tech-lead and architect reviews, and Ionel's answers to them, are applied. For Ionel's approval.
- **The brief:** `/Users/ionel/work/personal/telekom/Assessment_Task_Senior_Dev_Python_Agentic_AI.pdf`. Where this design departs from it, §12 says so and why. Anywhere else, the brief wins.
- **Background:** `/Users/ionel/work/personal/telekom/AGENT_GUIDE.md`, on what carries over from ARDE, Ionel's previous take-home, with pointers into its code.

Marks:
- **(Ionel):** Ionel decided it.
- **(Claude):** my call, which Ionel can overturn.
- **(verify):** a claim about Google's or Anthropic's APIs, to be checked on day 1 before anything is built on it.
- **[NEEDS CLARIFICATION]:** open. None are left.

---

## 1. What, and why

AECA watches a dedicated Gmail inbox. It does three things:
- it gives each new email one primary label;
- it takes the action the brief's table names for that label;
- for actions the table says need approval, it asks the manager by email first.

It is the assessment for Telekom's Senior Developer Python LLM role. The evaluators run it and send 20 emails it has never seen, to a calendar they have filled with events. Then they look at:
- the inbox;
- the calendar;
- the audit log.

That live run is the grade. Ionel will also explain each decision live, so the design prefers decisions that are easy to state and defend.

**Success.** For each of the 20 emails:
- the right label;
- the right action;
- the right approval behaviour;
- one audit line.

Nothing goes out that shouldn't, and the evaluators can start AECA from the README alone.

## 2. Goals, non-goals, anti-goals

**Goals:**
1. The brief's six scenarios, following its approval column.
2. Automatic replies, where the agent judges them warranted.
3. Meetings planned around existing events.
4. Approval by email: the manager replies APPROVE or REJECT.
5. One audit line per processed email. Failures are logged, and one never stops the loop.
6. Docker, configured by environment variables, and a README that uses only `make`.
7. A test-email generator that replicates the evaluators' test, a scorer, and a paid eval.
8. An onboarding page for Ionel, and docs for agents working on the repo.
9. A list of future ideas.
10. Production ready (Ionel). §4.12 says what that means here.

**Non-goals,** left out of the four days:
- the MCP server, which the brief makes optional;
- providers other than Anthropic, though the model stays behind a port so one can be added;
- languages other than English (Ionel);
- reading Gmail's Spam folder (Ionel);
- push notifications (AECA polls);
- reading what's inside attachments;
- a web UI;
- editing a draft by replying to the approval email.

**Anti-goals.** Any of these is a failure, even if everything else works:
- sending to an address the agent found in an email's text;
- acting on an APPROVE the manager did not write;
- a reply loop with another auto-responder;
- sending or creating anything twice, including after a restart;
- the model holding any tool that changes something;
- one bad email stopping the loop;
- two copies of AECA processing the same inbox;
- an API key, a token or an email body ending up in the image, the audit log or the console.

## 3. Constraints

- **The brief:** Python, a hosted LLM API, the Gmail and Google Calendar APIs, and Docker. The Gmail account and the credentials are set by environment variables.
- **Time:** four days, from Thursday 2026-11-05 to the handover on Sunday 2026-11-08, in the evening (Ionel).
- **Accounts:** a new consumer Gmail account and its Calendar (Ionel). The manager is that account's owner (Ionel). Ionel also creates two more Gmail accounts, used only to send test mail (Ionel).
- **Model:** Anthropic (Ionel).
- **What is handed over:** Ionel hands over only the Gmail and Calendar credentials. The evaluators bring their own Anthropic key; Ionel arranges it with them, and no fallback is built (Ionel).
- **Ionel's working rules:**
  - clean code, TDD and F.I.R.S.T. tests, with ports and adapters;
  - plans carry `[NEEDS CLARIFICATION]` markers and a Deviations section;
  - before writing tests for anything that can't be undone (sending mail, creating events, trashing test mail), get the test cases from the `tech-lead` agent.
- **Secrets:**
  - no agent reads, prints or commits `.env`; only Compose hands it to the container;
  - files are staged by path, never with `git add .`.

---

## 4. Behaviour

### 4.1 Labels

- **The brief's labels** are Gmail user labels with exactly its names: `MEETING_REQUEST`, `TASK`, `INFO_ONLY`, `SALES_OUTREACH`, `MARKETING` and `URGENT`.
- **Added:** `Finance`, and `UNKNOWN` for an email that couldn't be classified (Ionel).
- **SPAM is Gmail's own Spam folder.** A user label named `SPAM` is probably reserved (verify). If it isn't, AECA applies both.
- **AECA's own labels:**
  - `AECA/processed`;
  - `AECA/error`;
  - `AECA/awaiting`, kept on an email while its approval is open, so anyone looking at the inbox can see what is waiting.

### 4.2 The policy table

Code owns the policy. The approval column follows the brief's (Ionel).

| Primary label | When | Actions | Approval |
|---|---|---|---|
| MARKETING | newsletter, promotion, bulk mail | archive; may acknowledge (§4.3) | none |
| MEETING_REQUEST | a proposed time is free | create the event, then send the requester a confirmation (§4.4) | yes, one approval for both (Ionel) |
| MEETING_REQUEST | no proposed time is free, or none was proposed | a drafted reply proposing up to 3 free slots | yes (Claude) |
| MEETING_REQUEST | its invitation is already on the calendar (§4.4) | the label only | none (Claude) |
| SALES_OUTREACH | | a polite decline, then archive | none by default; `SALES_DECLINE_NEEDS_APPROVAL` turns it on (the brief says "configurable") |
| TASK | | the label; may acknowledge | none |
| INFO_ONLY | an invoice or receipt | add `Finance`, then archive | none |
| INFO_ONLY | anything else | the label; may thank | none |
| SPAM | | move to Spam, and log | none, and never a reply |
| UNKNOWN | classification failed (§4.7) | the label only | none (Ionel); no notification (Claude) |

The URGENT modifier is never combined with SPAM:

| Sender | Actions | Approval |
|---|---|---|
| in `INTERNAL_SENDERS` | star, the `URGENT` label, and a notice to the manager with a reply drafted to the sender | the reply waits for APPROVE (Ionel) |
| anyone, while `INTERNAL_SENDERS` is empty | the same as an internal sender (Ionel, after review) | the same |
| anyone else | star, and the `URGENT` label | none, and no draft (Ionel) |

- A primary label's actions and the modifier's actions add up. An urgent internal TASK gets both rows.
- **A `From` whose DMARC check failed never counts as internal (Claude),** so a forged internal address can't trigger the notice or the draft.
- **An empty `INTERNAL_SENDERS`** makes every urgent sender count as internal, so evaluators who forget the setting still see the brief's urgent-internal scenario. AECA logs a warning at startup when the list is empty. The reply rules in §4.3 still apply, so an automated sender gets the notice but no draft.

**What counts as urgent (Claude; Ionel can overturn).** The brief says "deadline or escalation". The URGENT record reads that from the manager's side: the manager must act soon, or something real is lost.
- **Urgent:**
  - a deadline today, or within the next two working days (the window is a guess, tuned with `make eval`);
  - an outage or an incident;
  - an escalation, such as "escalating this to you", a customer threatening to leave, or a second chase that got no answer.
- **Not urgent:**
  - urgency on the sender's side, such as "Sale ends tonight!" or "Offer expires Friday";
  - a deadline weeks away;
  - "URGENT" in a subject that nothing in the body supports.
- **The evidence:** the model quotes the text that makes the email urgent, and the validator checks the quote (§4.7). The `Importance` and `X-Priority` headers reach the model as a hint only, because marketers set them too.

**MARKETING is always archived (Claude).** The brief's row says "newsletter from known bulk sender → archive". The evaluators' emails will probably come from ordinary mailboxes, with no bulk headers. So every MARKETING email is archived. The bulk headers and the `BULK_SENDERS` list (Ionel) are used for two things:
- they are strong evidence for the label;
- they mark a sender that never gets an automatic reply.

They are not a condition for archiving.

**The policy is data (Claude, from Ionel's future idea).** Each label is one record, holding:
- its name, and a description and examples for the prompt;
- the decision fields it needs, and the reply kinds it allows. For example, a meeting needs its proposed times. The validator reads these from the record.
- its steps, in order. Each step is `{when, verb, args, approval}`:
  - `when` names a predicate from a fixed set, such as `always`, `slot_free`, `is_invoice`, `sender_internal`, `reply_warranted` or `invitation_on_calendar`;
  - `verb` names a verb from the fixed set below;
  - `approval` is `none`, `required`, or `setting:<NAME>`, as for the sales decline.

URGENT is a modifier record of the same shape.

Three things are generated from the records:
- the prompt's label section;
- the decision's list of allowed labels;
- the validator's rules.

A test checks that every predicate and verb a record names exists. The YAML loader will reuse that test later (§8). Version 1 keeps the records in code, and builds neither a loader nor an expression language.

**The verbs are a fixed set, implemented by code:** `apply_label`, `archive`, `move_to_spam`, `star`, `send_reply`, `notify_manager`, `request_approval` and `create_event`.
- A record combines verbs; it can't add one.
- The verbs that take an email out of the Inbox, `archive` and `move_to_spam`, always run last.

### 4.3 Automatic replies

**Who approves:**
- **Sales decline:** no approval (Ionel), unless `SALES_DECLINE_NEEDS_APPROVAL` is set.
- **Acknowledgement or thank-you:** the model decides whether one is warranted, and it needs no approval (Ionel). The brief's own examples are "a polite acknowledgement to a marketing offer or a brief thank-you where appropriate".
- **Urgent internal:** a reply is drafted, and it waits for approval (Ionel).

**What the model writes (Claude, after review).** The acknowledgement, the thank-you and the decline are code templates. The model only decides whether one is warranted, and which kind. The one reply the model writes itself is the urgent internal draft, and the manager reads that before it goes out. So no text written by a model leaves without a human seeing it.

**Limits, enforced by code (Claude):**
- **The recipient is the original `From` address.** `Reply-To` is ignored, because phishing uses it to redirect replies.
- **Never to SPAM, never to the account itself, and never to a `From` whose DMARC check failed.**
- **Never to an automated sender** (RFC 3834). That is one with:
  - `Auto-Submitted` set to anything but `no`;
  - `Precedence: bulk`, `list` or `junk`;
  - `List-Id` or `List-Unsubscribe`;
  - a sender on `BULK_SENDERS`;
  - a local part of `noreply`, `no-reply`, `mailer-daemon` or `postmaster`.
- **At most one reply per thread sent without approval.** Approved replies, such as a confirmation, don't count toward it.
- **The urgent draft comes from the decision call, which sees only that one email.** Code drops a draft that contains a link or an email address, or that runs past 800 characters.
- **It goes in the same thread,** using `threadId`, `In-Reply-To` and `References` (verify).
- **It carries `Auto-Submitted: auto-replied`** and AECA's own headers (§4.5), so other auto-responders don't answer it.

### 4.4 Meetings

- **The decision extracts the proposed windows.** Each has a start, an end if the email gives one, and the exact text it came from. The model gets "now" and the calendar's time zone, and resolves relative dates ("next Tuesday afternoon") against them. A time with no zone is read in the calendar's zone.
- **Code reads the calendar's time zone from Google Calendar** (verify). `TIMEZONE` overrides it. Every approval and confirmation states the zone, because the evaluators may sit in a different one (§10).
- **Code plans the slot (Claude, after review):**
  - **It reads the events in the windows** with `events.list(singleEvents=true)`, not free/busy. Free/busy ignores all-day events, which default to "free" (verify).
  - **Busy:** every event not declined or cancelled, including all-day events and invitations not yet answered. Holds are busy too, except the thread's own (below).
  - **Buffer and length:** it adds `BUFFER_MINUTES` around events. The meeting lasts what the email says, or else `MEETING_MINUTES`.
  - **Choice:** the earliest free slot in the first window that has one, in the order the times were proposed.
- **Holds (Claude, after review).** A hold is a slot in an approval nobody has answered yet, or one of the alternatives offered to a requester.
  - **Keyed by the email's thread.** Other threads see the slot as busy, so two requesters are never offered the same slot. The thread's own holds count as free for that thread, so a reply of "the second slot works" still fits.
  - **Released** on REJECT, when the approval is superseded, once it's executed, or when the slot starts.
- **Working hours:**
  - A proposed slot outside working hours is accepted if it's free, because the requester asked for it. The approval email notes that it falls outside working hours.
  - Alternatives are found only inside `WORK_HOURS`, on working days, within the next `ALTERNATIVE_DAYS`.
- **When nothing proposed is free, or no time was proposed (Claude):**
  - the approval email lists up to 3 free slots, with a reply that proposes them, and the 3 slots become holds for that thread;
  - APPROVE sends that reply, and no event is created yet;
  - the requester's answer arrives as a new email, and is planned like any other.
- **The event:**
  - its title comes from the decision;
  - its description holds the requester's name and address, the subject, a one-line summary, the quoted times and the approval id;
  - it has no attendees, so Google sends no invitation (Ionel).
- **The confirmation (Ionel).** Once the event exists, the requester gets a short reply in their thread, confirming the slot. Code fills its text from a template, the slot and the calendar's time zone (Claude). It is covered by the same approval as the event. If creating the event fails, no confirmation goes out.
- **On APPROVE, the calendar is checked again,** ignoring the approval's own hold. If the slot has filled in the meantime, AECA creates nothing, and sends a new approval with the next free slots.
- **Created once (Claude, after review).**
  - The event id is a hash of the source Gmail message id and an attempt number, encoded in base32hex.
  - Before inserting, AECA reads the event by that id. A live event means the work is done. A cancelled one means AECA moves to the next attempt number.
  - Google's docs don't promise to refuse a duplicate id, which is why the read comes first.
- **Real calendar invitations (Claude, after review).** Google's setting that adds invitations to the calendar automatically stays on. The evaluators may fill the calendar by sending invitations, and turning it off would keep their events off it.
  - An email whose invitation is already on the calendar is labelled MEETING_REQUEST, and needs no action. The audit line says so.

### 4.5 Approvals

- **What one is:** what the manager is asked to approve, as an ordered list of actions. Examples are creating an event and then confirming it, or sending one drafted reply. Each action has its own status and its own idempotency key, and they run in order. If one fails, the actions after it don't run.
- **Its id is derived from the source Gmail message id,** never a counter, so a fresh state store can never reuse an id (Claude, after review).
- **States:**
  - `awaiting → approved → executed | failed`;
  - `awaiting → rejected`;
  - `awaiting → superseded`, when the slot has filled.

  Approvals don't expire, but their holds do (§4.4) (Claude).
- **AECA's headers (Claude, after review).** Every message AECA sends carries `X-AECA-Kind` and `X-AECA-Id` (verify they survive sending).
  - AECA recognises its own mail by these headers together with `SENT`.
  - It recognises an approval thread by that thread's first message.
  - Neither check depends on the state store, so a lost volume can't make AECA mistake its own request for the manager's answer.
- **The manager's address (Ionel, after review).** `MANAGER_ADDRESS` when it's set, otherwise the AECA account itself. Approval requests, urgent notices and alerts all go there. With it set, the evaluators approve from their own mailbox and never need to log in to the AECA account, where Google may challenge a login from a new device (§11).
- **The request** is a new email from the account to the manager's address, in a thread of its own:
  - the subject is `[AECA] Approve: <action>`;
  - the body holds the original email's sender, subject and one-line summary, the exact action with its time zone, the draft when the action is a reply, and the line "To answer, reply with APPROVE or REJECT as the first word";
  - no line AECA writes, in any email, starts with APPROVE or REJECT.
- **Which reply counts.** A message in the request's thread, with no AECA headers, that passes one of two checks:
  - **From the account itself (Claude):** it carries Gmail's `SENT` label, which only the account can produce. Whoever holds the account is the manager, so this counts with or without `MANAGER_ADDRESS`. (Verify that a self-sent reply keeps `SENT` and stays in the thread.)
  - **From `MANAGER_ADDRESS`, when it's set (Ionel, after review):** Gmail's `Authentication-Results` shows that DMARC passed for it. A `From` alone can be forged; a DMARC pass can't be, without control of that address's domain. The README says the domain must publish DMARC, as Gmail, Outlook and most companies do. (Verify that the reply lands in the request's thread, and how the header reads.)
- **Reading the reply:**
  - Code reads the first word of the first line not quoted with `>`, ignoring case.
  - Anything other than APPROVE or REJECT is logged, and AECA replies once in the thread with how to answer.
  - A reply that passes neither check is ignored, and audited with the reason.
  - No model ever reads an approval reply.
- **On REJECT:** AECA logs it and does nothing else (Ionel). The original email keeps its label.
- **Exactly once (Claude, after review).** Every action, automatic or approved, goes through the one journal in §4.6.
  - **Sends go through Gmail drafts.** AECA creates the draft, records its id, then sends it. After a restart, a recorded draft that still exists gets sent. One that no longer exists was already sent.
  - **A reply first checks its thread.** Before sending, AECA reads the thread with `threads.get`, which reads the thread directly, unlike search, whose index can lag. If a sent message with the same `X-AECA-Id` is already there, AECA doesn't send. This covers a lost journal, and a second copy.
  - **Events:** §4.4.
  - **A failed approved action** is retried with the same keys before it is marked `failed`.
- **The README tells the evaluators** to set `MANAGER_ADDRESS` to their own address and approve from there, or to leave it empty and approve from the AECA inbox.

### 4.6 Which emails are processed

- **On each tick, the approval loop runs first,** then the inbox loop.
- **Polling:** every `POLL_SECONDS` (default 30), AECA reads Inbox messages without `AECA/processed`, oldest first (verify the query).
- **It skips AECA's own mail, and every approval thread (Claude).** AECA knows its own messages by its headers together with `SENT` (§4.5). Any other mail the account sends to itself is classified like any email, in case the evaluators send test mail from the account itself. Mail the account sends to other people never has `INBOX`, so it is never read. The approval loop reads only approval threads.
- **AECA labels its own messages, and each approval reply once read, `AECA/processed` (Claude),** so the inbox query doesn't return them on every poll.
- **Gmail's Spam folder is left alone (Ionel).** Mail that Gmail filtered there gets no audit line, and the README says so.
- **A Gmail filter keeps mail out of Spam (Ionel, after review).** It matches every message and is set to "Never send it to Spam" (verify which criteria match everything). Without it, Gmail could file the evaluators' phishing email in Spam before AECA sees it, leaving no label and no audit line. AECA still moves phishing to Spam itself.
  - Ionel creates it by hand on day 1, which needs no extra OAuth scope (Claude). The README tells anyone installing AECA on another account to do the same.
  - Gmail may still file mail it judges dangerous; the `send` rehearsal checks (§11).
- **One email at a time (Claude),** because holds need a fixed order. The rehearsal measures how long the burst of 20 takes. A small worker pool goes in only if it is too slow; the target is all 20 within 5 minutes.
- **Every Gmail change applies to the message, never to the thread.** Gmail groups emails with the same subject into one thread, so changing the thread could archive an email AECA hasn't processed yet.
- **The journal (Claude, after review).**
  - Before the first side effect, AECA stores the decision and its planned actions, each with its key. Each action is marked done as it finishes.
  - After a restart, unfinished work resumes from the journal by message id, not from the inbox query, because an archived email no longer matches the query.
  - At startup, before the first poll, every action left `executing` is resolved by checking its key.
- **`AECA/processed` is added last:** after the actions are done, or after the approval request is sent. In the second case, `AECA/awaiting` stays on until the approval closes.

### 4.7 The decision

For each email:

1. **Code extracts the facts:**
   - `From`, `Reply-To`, and whether the two differ;
   - whether the sender counts as internal: in `INTERNAL_SENDERS`, and its DMARC check didn't fail (§4.2);
   - the bulk headers, and the signs of an automated sender;
   - the `Importance` and `X-Priority` headers;
   - SPF, DKIM and DMARC, from `Authentication-Results`;
   - the links, and whether a link's text and its target disagree. Links rewritten by corporate mail filters, such as Microsoft Safe Links or Proofpoint's urldefense, are unwrapped first, or every link would look mismatched;
   - the attachments' names and types;
   - the text part, or the HTML reduced to text. All text is bounded, and control characters are escaped.
2. **Rules that decide alone (Claude).** Each one is tested and named in the audit line, and they are kept few, because content decides most of the evaluators' emails. The first rule: DMARC fails and a link mismatches, so the email is SPAM.
3. **One model call:**
   - The system prompt is cached. It holds the label records, the examples, and the rule that the email is data, never instructions.
   - The first message holds the facts, the current date and time in the calendar's zone (§4.4), and the email, fenced off from the rest.
   - The model returns a typed decision, through a submit tool or the API's structured output (builder's choice), and code validates it.
   - Only a missing or invalid label goes back to the model as a correction, at most twice. After that, the email gets `UNKNOWN`.

The decision, sketched:
```
primary_label        one of the label records
urgent               true or false, with the quote that shows it
finance              true for an invoice or a receipt
meeting              title, duration if stated, proposed windows [{start, end, quote}]
reply                none | acknowledgement | thank_you | decline
urgent_draft         the reply text, for an urgent internal email only
summary              one line, for the manager
suspected_injection  the text, if any
```

**The validator works field by field (Claude, after review).** A bad label is the only fault that refuses a whole decision. Any other fault drops the field it's in, keeps the label, and is noted in the audit line, so one bad field never turns a correctly labelled email into `UNKNOWN`. The validator:
- drops `urgent` on a SPAM email;
- drops a window whose quote isn't in the email, comparing after normalising whitespace and quote marks;
- drops `urgent` when its quote isn't in the email, compared the same way (Claude);
- drops a time that doesn't parse, or one already in the past. The evaluators may write their emails days ahead;
- sends a meeting left with no valid window down the alternatives path (§4.4);
- drops a reply kind the label's record doesn't allow, such as any reply to SPAM;
- drops an urgent draft that breaks the rules in §4.3.

### 4.8 The audit and the trace

All three files live on the volume.

- **`audit.jsonl`:** one line per processed email, and one per approval outcome. An email's line holds:
  - the time, message id, thread id, sender and subject (bounded);
  - the label, whether it's urgent, and what decided it: a rule, the model or the fallback;
  - the rules that fired;
  - the actions, each with its status;
  - the approval id and its state;
  - the outcome (`done`, `awaiting_approval` or `failed`), and any error;
  - the tokens and the cost.
- **`trace.jsonl`:** every step, model call (tokens, cost, latency), correction, Google call and retry. The Anthropic SDK's own retries are off (`max_retries=0`). The adapter retries 429s, 5xx errors and dropped connections itself, with backoff, and traces each attempt. In ARDE these retries never reached the trace.
- **`debug.log`:** the full text, email bodies included. It is off by default (§4.12). Bodies never go in the audit.
- **Secrets are redacted** by exact match before anything is written, as ARDE's tracer does.
- **Reading them:** `make audit` prints the audit, and stdout carries one summary line per email for `make logs`.

### 4.9 Failure

**Two kinds of error (Claude, after review):**
- **System-wide:** a refused key, a 429, a 5xx, a dropped connection, the cost cap.
  - These end the tick, and AECA backs off as a whole.
  - No email is charged for them, so an outage of a few minutes fails nothing.
- **Specific to one email:** for example, a body that can't be parsed.
  - The audit records `failed` with the error, and the email gets `AECA/error`.
  - The next polls retry it, up to 3 times. After that it keeps `AECA/error` and also gets `AECA/processed`.

**Other cases:**
- **The cost cap, `MAX_USD_PER_DAY` (default 5):** once it's hit, there are no more model calls that day. Emails wait, and the manager gets one notice.
- **At startup:**
  - AECA checks each credential: the Gmail profile, the Calendar and Anthropic.
  - A failure exits with the cause and the next step, as ARDE's errors do.
  - An expired or revoked Google token says so. A TLS failure names interception by a corporate proxy as a likely cause, and points to `EXTRA_CA_CERTS`.
  - The account's address must match `GMAIL_ADDRESS`, ignoring case and Gmail's dots.
  - A failure at startup never sends mail. Otherwise Docker's restarts would send one alert per restart, and Google suspends new accounts that send in bulk.

### 4.10 Configuration

| Variable | Default | What |
|---|---|---|
| `GMAIL_ADDRESS` | required | The AECA inbox. It must match the token's account. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` | required | OAuth credentials. `make auth` writes the token. |
| `ANTHROPIC_API_KEY` | required | The model key. The evaluators bring their own. |
| `MANAGER_ADDRESS` | empty | Where approval requests, urgent notices and alerts go, and the one outside address whose replies can approve (§4.5). Empty means the AECA account itself (Ionel). |
| `INTERNAL_SENDERS` | empty | Comma-separated addresses that count as internal (Ionel). Empty makes every urgent sender count as internal (§4.2). |
| `BULK_SENDERS` | empty | Addresses or domains that are always bulk (Ionel). |
| `TIMEZONE` | the calendar's | Overrides the calendar's zone. |
| `WORK_HOURS` | `Mon-Fri 09:00-17:00` | Where alternative slots are found. |
| `MEETING_MINUTES` | 30 | The length of a meeting when the email doesn't say. |
| `BUFFER_MINUTES` | 10 | The gap kept around events. |
| `ALTERNATIVE_DAYS` | 5 | How many working days ahead to look for alternatives. |
| `POLL_SECONDS` | 30 | How often the inbox is checked. |
| `SALES_DECLINE_NEEDS_APPROVAL` | false | Makes the sales decline wait for approval. |
| `MAX_USD_PER_DAY` | 5 | The cost cap. |
| `ALERT_AFTER_FAILURES` | 3 | How many emails in a row must fail before AECA alerts the manager. |
| `DEBUG_LOG` | false | Writes `debug.log`, with full email text. |
| `DEBUG_LOG_DAYS` | 7 | How long `debug.log` is kept, when it's on. |
| `EXTRA_CA_CERTS` | empty | A CA bundle to trust, for networks that intercept TLS. |
| `FORCE_LOCK` | false | Starts AECA even when another copy seems to hold the lock (§4.12). |

- **The model for each role** is set in a config file, as in ARDE's `models.yaml`. The test-email generator runs on a different model from the decision call.
- **Google scopes:** `gmail.modify`, plus whatever Calendar needs to list, read and insert events (verify the smallest set). The test tooling also needs `gmail.insert` (verify).

### 4.11 The container and the commands

- **The image:**
  - Python 3.12 slim, pinned by digest, with uv and locked dependencies;
  - a non-root user, `init: true`, a memory cap, and a named volume at `/data`;
  - secrets come only from `env_file`, at run time. A container test proves the key is in no image layer;
  - `make image` builds it locally from a multi-arch base image, so it runs on arm64 and amd64. No image is published (Claude);
  - `HTTPS_PROXY` is passed through when it's set, and the README says Windows needs WSL for `make` (Claude).
- **Every target starts with a preflight.** It checks `docker`, Compose, a running daemon, `.env`, and that each required variable is *set*. It never reads a value out.

| Target | What |
|---|---|
| `make auth` | A one-time OAuth consent in the browser. It writes the refresh token into `.env` without printing it. How it reaches a browser is the builder's choice. |
| `make image` | Builds the image. |
| `make start` / `make stop` | Starts AECA in the background, or stops it. |
| `make logs` | Follows the per-email summary lines. |
| `make audit` | Prints the audit log. |
| `make approvals` | Lists the approvals still waiting. |
| `make preflight` | Runs the checks alone. |

The test targets are in §5.

### 4.12 Production readiness (Ionel)

Already covered by the sections above:
- execute-once and restart safety (§4.4, §4.5);
- failure isolation, retries and the cost cap (§4.9);
- the startup checks (§4.9);
- the audit and trace, with secrets redacted (§4.8);
- a hardened image (§4.11);
- the injection defences (§4.3, §4.7);
- the tests (§5).

Added to reach production ready (Ionel agreed the list; the mechanisms are Claude's):

1. **It runs unattended.**
   - Compose has `restart: unless-stopped`.
   - After each successful poll, AECA writes a heartbeat to the volume. The image's `HEALTHCHECK` fails when the heartbeat is older than 3 × `POLL_SECONDS`.
   - On SIGTERM, AECA finishes the email in hand, then exits. Compose's `stop_grace_period` is set long enough for the worst case: three model calls and the Google calls after them.
2. **It says when it's broken.** It sends one alert email to the manager per incident, not one per email. The incident state is kept on the volume, so a restart doesn't send the alert again. The incidents are:
   - the cost cap was hit;
   - the Anthropic key was refused;
   - `ALERT_AFTER_FAILURES` emails in a row failed (default 3).

   When Gmail itself is what fails, for example a revoked token, no email can go out. The only signals left are the health check, the logs and a non-zero exit.
3. **Logs an operator can use.**
   - Every log line on stdout is JSON.
   - `audit.jsonl` and `trace.jsonl` rotate by size, and a set number of files is kept.
   - `debug.log` holds full email text, which is personal data. It is off by default (`DEBUG_LOG=false`). When on, it is kept for `DEBUG_LOG_DAYS` days (default 7).
4. **CI.** GitHub Actions runs:
   - ruff, and strict mypy;
   - the offline suite and the architecture test;
   - the image build;
   - `pip-audit` over the locked dependencies.

   It needs no secrets, and it never calls Google or Anthropic.
5. **Safe upgrades.** The SQLite schema carries a version (`PRAGMA user_version`), checked at startup. A version 1 needs no migration framework. A schema change ships with its migration, and an upgrade never drops a pending approval (Claude, after review).
6. **One copy per inbox.** The lock must live in the Google account itself, because that is the only thing two copies on different machines share.
   - **The proposal:** a Gmail draft titled `AECA lock`, holding the copy's id and a heartbeat (verify that drafts can be updated this way), behind its own `Lease` port. The builder may choose another mechanism in the account.
   - **At startup,** a heartbeat younger than 3 × `POLL_SECONDS` from another copy stops AECA, and names the next step: stop the other copy, wait, or set `FORCE_LOCK`. Refusing to start in front of the evaluators would be worse than the risk the lock prevents.
   - **On every tick,** before acting, AECA checks it still holds the lock. A laptop that slept past the heartbeat finds it lost the lock, and stops.
   - **The lock is a courtesy, not the guarantee (Claude, after review).** Gmail drafts have no compare-and-swap, so two copies starting at the same moment can both take it. Behind the lock, keys derived from Gmail message ids let a second copy find work already done:
     - for events, by reading the event id (§4.4);
     - for replies, by checking the thread (§4.5).

     One gap remains. A new approval request has no thread yet, so two copies racing at the same moment could each send one. The manager would then see two requests, and the second APPROVE would find the event already created.

**Limits, named in the README and in `docs/`, not built (Ionel):**
- AECA sends the inbox's contents to Anthropic. In production that needs a data-processing agreement, and a decision about where the data is processed.
- Secrets come from `.env`, not from a secret manager.
- AECA polls. At scale it would use Gmail push notifications and the history API.
- AECA serves one inbox per copy.

---

## 5. Testing

- **The offline suite.** It has fakes for every port and a scripted model. It covers:
  - every policy path, and the planner;
  - the approval parser and its states;
  - loop prevention;
  - an architecture test that fails if a layer is broken.

  It runs in milliseconds, with no network.
- **The test-email generator (Ionel).** A separate agent writes a corpus that replicates the evaluators' test. It is a script run once to produce the corpus, not a tool to maintain, because the committed corpus is what matters (Claude, after review).
  - **What it sees:** the brief's scenarios and labels, and a list of edge cases, but never AECA's prompt. It runs on a different model from the decision call.
  - **What it writes:** `tests/corpus/emails.json`. Each email has:
    - a sender role (internal, external, bulk or automated), the sender, the subject, the body and the headers;
    - the expected label, urgency, actions, approval and reply.

    Meeting emails refer to the seeded calendar, and say whether a time should be free, should clash, or whether every time clashes.
  - **Checked once by Ionel.** Ionel reviews the expected answers once, and the corpus is committed, so every run uses the same emails.
  - **About 30 emails:** each scenario written several ways, each with and without the headers a real sender would add, plus these edge cases:
    - relative dates, and explicit time zones;
    - every proposed time clashing, and no time proposed;
    - an invoice with a PDF attachment;
    - a sales follow-up in a thread;
    - an urgent email from outside, and an urgent internal meeting request;
    - urgency only on the sender's side ("Sale ends tonight!"), and a deadline weeks away;
    - a phishing email from a lookalike domain;
    - an email that orders the agent to forward mail, or to approve something;
    - a `noreply` sender;
    - an HTML-only email;
    - an email in a thread AECA has already replied to;
    - two emails with the same subject, which Gmail puts in one thread;
    - an out-of-office reply that carries no auto-reply headers;
    - a link rewritten by Safe Links.
- **`make seed-calendar`** fills a test week with events, each tagged as test data. It includes all-day, recurring, tentative and declined events.
- **`make reset-test`** cleans the rehearsal out of the account. The rehearsal runs on the handover account itself (Ionel), so this is what leaves it clean. It is destructive, so its test cases come from `tech-lead` first. It:
  - only moves to Trash, never deletes. Gmail empties Trash after 30 days, so until then the rehearsal is visible there, and Ionel's handover message says so (Claude);
  - touches only what the rehearsal made (Claude):
    - the seeded events, which carry a test tag;
    - mail from or to the two sender accounts, which exist only for testing;
    - mail that `insert` mode put in, labelled `AECA/test` when inserted;
    - what AECA did in response, found by the ids it derives from those emails' message ids: the approval threads and the events (§4.4, §4.5);
  - refuses to run while a copy of AECA holds the lock (§4.12), so it can't run during the evaluators' session (Claude);
  - shows what it would do (a dry run) before doing it. Afterwards, a second dry run must find nothing left.

**Test cases that block a release (from the tech-lead review).** These are safety cases, run offline with fakes:
1. **Crash between a Google write and the state write.** Kill AECA after each such write: the approval request, an automatic reply, the event, the confirmation. Restart it, and restart it again with the volume wiped. Each write must happen exactly once.
2. **The calendar misbehaves.** One fake calendar holds a cancelled event under the derived id, and another accepts duplicate ids. Neither may lead to a confirmation without a live event, or to a second event.
3. **The re-check on APPROVE ignores the approval's own hold.** Otherwise the approval supersedes itself, and mails a new one, on every poll.
4. **AECA's own words never approve.** With the recorded ids lost, and the sender's display name or the subject set to "APPROVE", AECA's own requests and instruction emails never count as an approval.
5. **Forged approvals are ignored and audited.** An "APPROVE" without `SENT` in the thread is ignored, including one whose `From` is spoofed as the account. With `MANAGER_ADDRESS` set, an "APPROVE" from that address whose DMARC failed, or one outside the request's thread, is ignored too.
6. **A credential failure across N restarts sends no mail.**
7. **Archiving one email leaves its neighbour alone.** Archiving or spamming message B leaves its unprocessed sibling A, in the same thread, in the Inbox.
8. **Replies AECA must not send.** No unapproved reply to the account itself, and none to a `From` whose DMARC failed. An out-of-office with no auto-reply headers gets one reply at most.
9. **`make reset-test` stays inside its limits:** it only moves mail to Trash, touches only what the rehearsal made (an email the evaluators send to the same inbox is left alone), refuses while a copy of AECA holds the lock, and runs dry first.

**Correctness cases:**
- **Approval replies:**
  - variants of the word: "Approve.", "APPROVED";
  - a leading blank line, or a non-breaking space;
  - a reply in HTML only;
  - a reply written below the quoted text;
  - two answers in one thread.
- **The planner:** recurring, declined, tentative and all-day events.
- **Choosing an alternative:** the requester picks the second of three offered slots, with the original email quoted below their answer.
- **Urgency:** with `INTERNAL_SENDERS` empty, an urgent email from any sender gets the notice and a draft; a listed `From` whose DMARC failed is treated as external; an urgent quote that isn't in the email drops `urgent`.
- **`GMAIL_ADDRESS`** differing from the account only by case, or by Gmail's dots.
- **`make eval`** runs the decision on the corpus, with no Gmail involved. It reports accuracy per label, a confusion matrix and the cost. It is opt-in and costs money, and it is the main loop for tuning the prompt.
- **`make rehearse MODE=send|insert`** seeds the calendar, then delivers the corpus:
  - **`send`** is the main mode (Ionel, after review). It sends real mail from the two sender accounts Ionel creates, one of them listed in `INTERNAL_SENDERS`. Only real sending meets Gmail's spam filter, real authentication results and real threading, as the evaluators' mail will. Gmail won't send as an address the account doesn't own, so the cases that need a `noreply` sender or a lookalike domain run only in `make eval` and the offline suite;
  - **`insert`** uses `messages.insert`, with any sender and any headers (verify). It is the first thing cut (§7);
  - the rehearsal runs with `MANAGER_ADDRESS` set to an address Ionel controls, as the evaluators will most likely run it (Claude).
- **`make score`** runs after AECA has processed the rehearsal. It reads the labels, the audit, the replies sent, the approvals and the calendar, and prints a pass or fail per email and the accuracy per label.
- **Every real run is logged,** with its cost and what it found, as in ARDE's `first-real-run.md`.
- **Before the handover:**
  - stop Ionel's own copy, so `make reset-test` can run and the evaluators' copy can take the lock;
  - `make reset-test`, and clear the state volume;
  - look at the Inbox, Sent and the calendar: nothing from the rehearsal is left outside Trash;
  - check that the never-Spam filter is in place (§4.6), and that the token is valid;
  - run every README command exactly as written, on a clean checkout, then stop that copy too.

## 6. Docs

- **README:** uses only `make`. It covers:
  - what AECA does, the setup and the `.env` table;
  - "Before you start": the settings the evaluators may change (`MANAGER_ADDRESS`, `INTERNAL_SENDERS`, `TIMEZONE`), and what AECA assumes for the rest (§10);
  - how to approve, and where the audit is;
  - what AECA leaves alone, such as the Spam folder;
  - where it departs from the brief.
- **DESIGN.md:** two pages, in the shape of ARDE's.
- **`AGENTS.md`**, as the entry point, with a `docs/` wiki behind it:
  - the architecture and a glossary;
  - one record per decision, including what was rejected;
  - the limitations;
  - a guide for each day.

  After each feature, a pass removes any lines it made false.
- **The onboarding page for Ionel:** an Artifact, like ARDE's. It covers the flow, each decision and why, where each piece lives, and how to demo it. It's updated at the end of each day.
- **Future ideas:** in DESIGN.md and on the onboarding page.

## 7. Four days

The builder may adjust these. Day 4 ends with the handover on Sunday evening, so it is short.

| Day | Delivers | Needs from Ionel |
|---|---|---|
| 1, Thursday | The Google project, the consent screen in production, and `make auth`. A spike for each (verify) in §11. One email → label → audit line, in Docker, with each email's failure kept to that email from the start. | The Gmail account and its calendar's time zone, the Cloud project, the consent click, and the never-Spam filter |
| 2, Friday | The decision call, the policy records, all six scenarios, the replies and the planner. The test-email generator, the corpus and `make eval`. The calendar seeding, and in the evening a first `send` rehearsal for the labels and the automatic actions, checked by eye (Ionel, after review). | Checking the corpus's expected answers, and the two sender accounts |
| 3, Saturday | The approval loop with `MANAGER_ADDRESS`, the urgent drafts, the re-check and execute-once. The full rehearsal, with approvals, and the scorer. | Approving during the rehearsal |
| 4, Sunday | Failure handling, the cost cap, hostile emails, the README, DESIGN, the docs, and the handover. | The credential handover |

Each day has one review and one fix round, and ends with the onboarding page and the day's guide updated.

**Saturday is the riskiest day** (from the tech-lead review). It carries the approval loop, execute-once, supersede, the lock, the health check, shutdown, the full rehearsal and the scorer. The seeding and the first `send` run already moved to Friday; pull forward whatever else can move.

**If the days slip, cut in this order (Ionel):**
1. `insert` mode (Ionel, after review). The rehearsal then sends real mail only.
2. The scorer. Results are then checked by eye with `make audit`.
3. The daily onboarding updates. The page is then written once, at the end.

**Never cut:** the approvals, failure handling, production readiness (§4.12), the README and the handover. Production readiness is on this list because Ionel made it a requirement (Claude).

**Where the production items land (Claude):**
- CI and JSON logs on day 1, because they cost least at the start;
- the copy lock, the health check and the graceful shutdown on day 3, with the loop's hardening;
- the alerts, log rotation, debug-log retention and migrations on day 4.

## 8. Future ideas

- **Rules a user can edit (Ionel):** the policy records load from a YAML file, which is validated when it loads.
- **New labels, defined in a structured way (Ionel):** each is a record with a name, a description, examples, its verbs and its approvals.
- Gmail push notifications through Pub/Sub, instead of polling.
- Learning from the manager: when they relabel an email, keep it as an example for that sender.
- A second, independent classification when the model is unsure, taking the safer label if the two disagree.
- Reading PDF invoices.
- Approving with edits: the manager edits the draft in their reply.
- Other languages, the MCP server, and Azure OpenAI.
- A daily summary email.

## 9. Left to the builder

- Libraries within the stack. No agent framework is required; ARDE's `run_agent` pattern is small.
- A submit tool, or the API's structured output.
- The model for each role, chosen by `make eval`.
- The prompt text, the file layout and the SQLite schema.
- How `make auth` reaches a browser.

## 10. The evaluators' setup

AECA needs no answers from the evaluators, so no questions are sent (Ionel). Each point below works whatever they do, or is a setting they choose themselves. The README's "Before you start" section states them (§6).

- **Where the 20 emails come from.** Several mailboxes, one, or the AECA account itself all work (§4.6). If they send all 20 from one mailbox, the never-Spam filter should stop Gmail from filing that sender's later mail in Spam after the phishing email; the `send` rehearsal checks (§11).
- **Internal senders.** They list them in `INTERNAL_SENDERS`. If they leave it empty, every urgent sender counts as internal (§4.2).
- **Who approves.** They set `MANAGER_ADDRESS` to their own address and approve from their own mailbox, or leave it empty and approve from the AECA inbox. In the second case, the handover says how to log in, because Google may challenge a login from a new device (§11).
- **OS and CPU.** The image builds locally for arm64 and amd64 (§4.11). On Windows, `make` needs WSL.
- **Time zone.** AECA uses the calendar's zone, states it in every approval and confirmation, and `TIMEZONE` overrides it. Ionel sets the AECA calendar's zone on day 1: where the evaluators work, if known, otherwise Ionel's own.
- **How the calendar is filled.** Events created directly and invitations both work (§4.4).
- **The Anthropic key** is the one thing AECA can't run without. Ionel arranges it with them (§3).

## 11. To verify on day 1

Each item gets a spike against the real account. Each result is recorded in `docs/` before anything is built on it.

1. **Answered by Google's docs** (the tech-lead review checked them): in Testing mode a refresh token lasts 7 days, so the consent screen goes to "In production". A password change revokes refresh tokens that carry Gmail scopes, so the account's final password is set before `make auth`. Still to check: whether the unverified-app warning is acceptable for a single user.
2. The smallest set of OAuth scopes for reading, labelling, archiving, moving to Spam, starring, sending, inserting test mail, and listing, reading and inserting events.
3. Whether a user label named `SPAM` is reserved.
4. A reply sent with `threadId`, `In-Reply-To` and `References` lands in the sender's thread.
5. An email the account sends to itself keeps both `SENT` and `INBOX`, and the manager's reply to it stays in the thread, with `SENT`.
6. The Inbox query that excludes `AECA/processed`.
7. Custom `X-AECA-*` headers survive sending through `drafts.send`, and `threads.get` returns them. A sent draft no longer exists afterwards. Gmail may rewrite a custom Message-ID (from forum reports), which is why AECA doesn't rely on one.
8. `messages.insert` accepts any `From` and custom headers. Only needed if `insert` mode is built.
9. `events.list(singleEvents=true)` returns all-day events marked as free (this takes five minutes to check), and AECA can read the calendar's time zone.
10. `events.get` on a client-chosen id, for an event that was never created, one that is live, and one that was cancelled. Google's docs say a duplicate id may not be refused at insert time.
11. Anthropic: structured output or a forced tool call, together with the model chosen; and `max_retries=0`.
12. An invitation email for an event Google already added to the calendar can be matched to that event, for example by its iCalUID.
13. Whether moving one sender's email to Spam makes Gmail file that sender's later mail in Spam. If it did, an evaluator who sends all 20 from one mailbox would lose every email after the phishing one. The never-Spam filter (§4.6) should prevent it; the `send` rehearsal checks, and if it doesn't hold, the README warns them.
14. A Gmail draft can be updated in place, to hold the copy lock's heartbeat (§4.12).
15. A login to the account from a clean browser, over a VPN in another country. Record what Google asks for, because evaluators who leave `MANAGER_ADDRESS` empty will meet it.
16. A reply to an approval request from an outside mailbox lands in the request's thread in the AECA account, and its `Authentication-Results` shows `dmarc=pass` for its `From`.
17. The never-Spam filter: which criteria match every message, and whether it holds for a phishing-like email sent in `send` mode.

## 12. Deviations

**From the brief:**
- **`UNKNOWN` is a seventh primary label** (Ionel).
  - Why: a failed classification must not be guessed as one of the six.
  - Cost: the brief says one of six labels, so the README explains why.
- **The sales decline and the acknowledgements go out without approval** (Ionel).
  - Why: they follow the table's approval column, and the brief's line that "the agent decides" when a reply is warranted.
  - Cost: this contradicts the requirement for "manager approval via email reply before any send". The README states the choice.
- **Mail that Gmail itself files in Spam gets no audit line** (Ionel).
  - Why: AECA watches only the Inbox.
  - Cost: "every processed email" holds, but not every email that arrives is processed. With the never-Spam filter (§4.6), that should be only mail Gmail judges dangerous enough to override it. The README says so.
- **The model is Anthropic, not Azure OpenAI** (Ionel).
  - Cost: the evaluators must bring an Anthropic key, and the brief's hint about substituting their AIS credentials doesn't apply. Ionel arranges the key with them, and no Azure OpenAI fallback is built (Ionel).
- **No MCP server** (Claude). The brief makes it optional, and four days don't fit it.
- **MARKETING is archived whether or not the sender is a known bulk sender** (Claude). §4.2 explains why.

**From Ionel's conventions:**
- **What I checked:** argument counts, comments, test independence, wrapping libraries, and new top-level directories. This is a new repo, so the last doesn't apply. No deviations are planned.
- **The exception:** `make eval` and `make rehearse` use the network and cost money. They are opt-in and kept out of the fast suite, as ARDE's `make eval` was.
