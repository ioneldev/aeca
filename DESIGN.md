# AECA design

AECA watches one Gmail inbox. It labels each new email, takes the action a policy names for that label, and asks the manager by email before any action that needs approval. One command, in a container: `make start`.

## Flow

**Code decides; the model reads.** The policy, the limits on replies, the approval rules and the order of steps are code that no model can skip. The model does one thing: it reads one email and returns a typed decision. It holds no tool that changes anything, and no model ever reads an approval reply.

```
facts (code) → rules that decide alone (code) → one model call → validate (code)
→ plan the actions (code, from the policy records) → journal → execute
→ approval loop → audit line
```

| Step | Kind | What it does |
|---|---|---|
| Facts | code | From, Reply-To, DMARC/SPF/DKIM, bulk and automated signs, links and whether they mislead, attachment names, bounded text |
| Rules | code | A few named rules decide alone, such as DMARC fails and a link misleads, so SPAM |
| Decide | one model call | A cached system prompt with the label records; the email fenced off as data; a typed decision back |
| Validate | code | Field by field: a bad label is the only fault that refuses the decision; a bad field is dropped and noted |
| Plan | code | The label's policy record gives ordered steps; the planner picks the slot from calendar events |
| Journal | code | The decision and every planned action, each with its key, are stored before the first side effect |
| Execute | code | Each action runs once, from its key |
| Approve | code | The manager replies APPROVE or REJECT; the held actions run or don't |

Why this split: the labels are a judgement, so a model makes them. What goes out in a person's name is a risk, so code decides who may be answered, and the only text a model writes is the urgent draft, which the manager reads first ([0022](docs/decisions/0022-the-model-writes-only-the-urgent-draft.md), [0023](docs/decisions/0023-limits-on-automatic-replies.md)). The policy is data, one record per label, so a new label is a record, not a branch ([0021](docs/decisions/0021-policy-is-data.md)).

## Context strategy

- The system prompt is cached: label records, examples, and the rule that the email is data.
- The first message holds the facts, the date and time in the calendar's zone, and the email, bounded and fenced.
- One email is seen per call. The urgent draft is written from that one email only.
- Only a missing or invalid label goes back to the model, at most twice. After that the email is `UNKNOWN` ([0010](docs/decisions/0010-unknown-and-finance-labels.md)).

## Verification

- **Quotes.** An urgent flag and each proposed meeting window must quote the email. The validator checks the quote after normalising whitespace and quote marks.
- **Approvals.** A reply counts only if it is in the request's thread, has none of AECA's headers, and is either sent from the account itself (Gmail's `SENT`) or is from `MANAGER_ADDRESS` with a DMARC pass for its own domain ([0029](docs/decisions/0029-which-reply-counts-as-an-approval.md)).
- **Exactly once.** One journal; sends go through Gmail drafts; a reply first checks its thread for its own `X-AECA-Id`; an event id is derived from the source message id and read before it is inserted ([0031](docs/decisions/0031-every-action-goes-through-one-journal.md), [0026](docs/decisions/0026-event-is-created-once-from-a-derived-id.md)).
- **Offline tests.** Fakes for every port and a scripted model. An architecture test fails if a layer imports what it must not.
- **Measured.** `make eval` on the 35-email corpus: 100% on every label, urgent recall and precision 5/5, about $0.20. The first `send` rehearsal: 27 emails in about 3 minutes for $0.16 ([0002](docs/decisions/0002-first-live-run.md), [0003](docs/decisions/0003-first-send-rehearsal.md)).

## Defences

An email is untrusted text. Against a hostile one:
1. It is data: bounded, control characters escaped, fenced, and the prompt says so.
2. The model has no write tool, so an injection can only change a label or a field the validator then checks.
3. Replies go only to the original `From`, never `Reply-To`, never to an automated sender, a failed-DMARC sender or the account itself, and at most one unapproved reply per thread.
4. A reply can't approve itself: AECA's own words never count, and no line it writes starts with APPROVE or REJECT.

## Known limitations, and one more week

- English only. The Spam folder isn't read. Attachments aren't opened.
- One email at a time, by polling, one inbox per copy.
- The inbox's contents go to Anthropic, and secrets come from `.env`.
- A state volume wiped between an event and its confirmation leaves the confirmation unsent: at most once, not exactly once.

Full list: [docs/limitations.md](docs/limitations.md).

## Future ideas

- **Rules a user can edit (Ionel):** the policy records load from a YAML file, validated on load.
- **New labels (Ionel):** each a record with a name, description, examples, verbs and approvals.
- Gmail push notifications through Pub/Sub, instead of polling.
- Learning from the manager: a relabel becomes an example for that sender.
- A second, independent classification when the model is unsure, taking the safer label if the two disagree.
- Reading PDF invoices.
- Approving with edits: the manager edits the draft in their reply.
- Other languages, the MCP server, and Azure OpenAI.
- A daily summary email.
