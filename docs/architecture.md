# Architecture

The short map. The reasoning is in [DESIGN.md](../DESIGN.md) and the [decision records](decisions/README.md).

**Status:** Built and run: the inbox loop, the decision, the policy and planner, the approval loop, the journal, startup checks, the cost cap, incident alerts, the corpus, `make eval`, `make seed-calendar` and `make rehearse`. <!-- Task 15 --> <!-- Task 17 --> The health check, the copy lock and the debug log (Task 15) and `make reset-test` (Task 17) are not described here yet.

## Layers

Ports and adapters. Each layer is a folder under `src/aeca/`, and `tests/test_architecture.py` reads every module's imports and fails when a layer imports what it must not.

| Layer | Holds | May import |
|---|---|---|
| `domain/` | Frozen models and pure rules, with no I/O | the domain, and pydantic |
| `application/` | The flow, and the ports it needs | the domain and the application |
| `adapters/` | Gmail, Calendar, Anthropic, SQLite, logs: one adapter per port | anything |
| `tools/` | The test commands: corpus, eval, calendar seeding, rehearsal | anything |
| `cli.py`, `wiring.py`, `settings.py` | The commands, the one place adapters meet ports, and the typed settings | anything |

**Domain** (`src/aeca/domain/`): `email`, `facts`, `auth_results`, `links`, `text`, `rules`, `decision`, `validation`, `labels`, `policy`, `actions`, `planner`, `time_range`, `calendar_event`, `meeting_outcome`, `approval`, `answers`, `approval_messages`, `replies`, `compose`, `templates`, `outgoing`, `journal`, `ids`, `audit`, `errors`, `secret`.

**Application** (`src/aeca/application/`): `service` (the tick), `process_email` (one email), `decide`, `decision_prompt` and `prompts/`, `meetings`, `executor`, `approvals`, `deferred_approval`, `watch`, `startup`, `budget`, `incidents`, `failures`, `ports` and `state_ports`.

**Ports:** `Clock`, `Tracer`, `LLMClient`, `Mailbox`, `Calendar`, `ApprovalOpener`, `StateStore`, `AuditLog`, `IncidentReporter`, `SpendLedger`.

**Adapters** (`src/aeca/adapters/`):

| Port | Adapter |
|---|---|
| `LLMClient` | `anthropic_client` (retries 429s, 5xx and dropped connections itself, with `max_retries=0` on the SDK), `pricing`, `models_file` |
| `Mailbox` | `gmail` |
| `Calendar` | `google_calendar` |
| Google plumbing | `google_auth`, `google_services`, `google_errors`, `google_seed_calendar` |
| `StateStore` | `sqlite_state` (schema version in `PRAGMA user_version`) |
| `AuditLog`, `Tracer` | `audit_log`, `tracer`, `json_log`, `rotating_log`, `redactor` |
| `Clock` | `clock` |
| Console and `.env` | `console`, `env_file` |

## The per-email flow

`Service.tick` runs the approval loop, then reads Inbox messages without `AECA/processed`, oldest first, and hands each to `InboxProcessor.process`.

1. **Facts.** `extract_facts` reads the raw message: sender, DMARC, bulk and automated signs, links, attachments, bounded text.
2. **Skip.** AECA's own mail (its headers plus `SENT`) and approval threads are labelled processed and not classified ([0032](decisions/0032-skip-own-mail-and-approval-threads.md)).
3. **Rules.** `rule_verdict` can decide alone ([0034](decisions/0034-a-few-rules-decide-alone.md)).
4. **Decide.** Otherwise `decide` makes one model call through `LLMClient`, behind the `Budget`.
5. **Validate.** `validate` checks field by field and returns a `Decision`, or refuses the label ([0035](decisions/0035-the-validator-works-field-by-field.md)). A refused label goes back to the model, at most twice.
6. **Plan.** `plan_meeting` reads the calendar for a meeting; `plan_actions` turns the decision, the facts and the meeting outcome into the actions that run at once and the actions that wait for approval.
7. **Journal.** The decision and the planned actions are stored, each with its key.
8. **Execute.** `Executor.run` performs each action once.
9. **Approve.** Actions that need approval become one approval, and `request_approval` sends the request.
10. **Audit.** One `AuditLine`. `AECA/processed` is added last, and `AECA/awaiting` stays while an approval is open.

A failure ends one of two ways ([0036](decisions/0036-two-kinds-of-error.md)). A system-wide one, such as a refused key, a 429, a 5xx or the cost cap, ends the tick, and AECA backs off as a whole. An error in one email is audited as `failed`, the email gets `AECA/error`, and the next polls retry it up to 3 times.

## The approval loop

`ApprovalService.process_all` reads the thread of each open approval.

1. A message counts as an answer only if it has none of AECA's headers and passes one of two checks: Gmail's `SENT` (from the account itself), or `MANAGER_ADDRESS` with a DMARC pass for that same domain. `why_not_an_answer` names the reason when it doesn't, and the reply is audited and ignored.
2. `parse_answer` reads the first word of the first unquoted line.
3. APPROVE: the approval's actions run in order through the executor. On a meeting, the calendar is checked again first, ignoring the approval's own hold. If the slot has filled, the approval is superseded and a new one is sent.
4. REJECT: logged. Nothing else happens ([0030](decisions/0030-reject-does-nothing.md)).
5. An unclear answer gets one help reply.

States: `awaiting → approved → executed | failed`; `awaiting → rejected`; `awaiting → superseded`. Holds are released on REJECT, on supersede, on execution, and when the slot starts.

## Exactly once

| Mechanism | What it covers | Where |
|---|---|---|
| **The journal** | Work resumes by message id after a restart. At startup every action left `executing` is resolved by its key. | `domain/journal.py`, `application/startup.py`, `adapters/sqlite_state.py` |
| **Drafts** | A send creates a draft, records its id, then sends it. After a restart a recorded draft that still exists is sent; one that is gone was already sent. | `application/executor.py` |
| **The thread check** | Before a reply, `threads.get` looks for a sent message with the same `X-AECA-Id`. This covers a lost journal. | `application/executor.py` |
| **The 25-recent-sent check** | A message in a thread of its own (an approval request, an urgent notice, an alert) has no thread to check, so after a wiped volume it is looked for among the 25 most recent sent messages. | `application/executor.py` |
| **Derived ids** | The approval id, the event id and the action keys are hashes of the source message id, never counters. | `domain/ids.py` |
| **Read before insert** | An event is read by its id first. A live event means the work is done; a cancelled one moves to the next attempt number. | `application/executor.py` |

Why the order: [0031](decisions/0031-every-action-goes-through-one-journal.md), [0026](decisions/0026-event-is-created-once-from-a-derived-id.md), [0027](decisions/0027-approval-ids-and-headers-need-no-state-store.md). One limit remains: [limitations](limitations.md).

## The files

| File | Where | What |
|---|---|---|
| `state.db` | `/data` volume | Approvals, holds, the journal, spend, incidents |
| `audit.jsonl` | `/data` volume | One line per email and per approval outcome |
| `trace.jsonl` | `/data` volume | Every step, model call, Google call and retry |

## The commands

`aeca auth`, `run`, `audit`, `approvals`, `eval`, `seed-calendar`, `rehearse` and `reset-test`, in `cli.py` and `tools/tool_commands.py`. The `make` targets call them; `make preflight` is `scripts/preflight.sh`.
