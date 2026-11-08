# Architecture

The short map. The reasoning is in [DESIGN.md](../DESIGN.md) and the [decision records](decisions/README.md).

**Status:** Built and run: the inbox loop, the decision, the policy and planner, the approval loop, the journal, startup checks, the cost cap, incident alerts, the corpus, `make eval`, `make seed-calendar`, `make rehearse`, the health check, the copy lock, graceful stop, `make reset-test` and the debug log (`application/decide.py` writes the email as the model sees it, each raw reply, and the text of a suspected injection).

## Layers

Ports and adapters. Each layer is a folder under `src/aeca/`, and `tests/test_architecture.py` reads every module's imports and fails when a layer imports what it must not.

| Layer | Holds | May import |
|---|---|---|
| `domain/` | Frozen models and pure rules, with no I/O | the domain, and pydantic |
| `application/` | The flow, and the ports it needs | the domain and the application |
| `adapters/` | Gmail, Calendar, Anthropic, SQLite, logs: one adapter per port | anything |
| `tools/` | The test commands: corpus, eval, calendar seeding, rehearsal, reset-test | anything |
| `cli.py`, `wiring.py`, `settings.py` | The commands, the one place adapters meet ports, and the typed settings | anything |

**Domain** (`src/aeca/domain/`): `email`, `facts`, `auth_results`, `links`, `text`, `rules`, `decision`, `validation`, `labels`, `policy`, `actions`, `planner`, `time_range`, `calendar_event` (the event as the planner and reset-test read it), `meeting_outcome`, `approval`, `answers`, `approval_messages`, `replies`, `compose`, `templates`, `outgoing`, `journal`, `ids`, `audit`, `errors`, `secret`, `lease` (who holds the inbox), `rehearsal_mail` and `reset_lock` (what reset-test may touch, and when it must refuse).

**Application** (`src/aeca/application/`): `service` (the tick), `process_email` (one email), `decide`, `decision_prompt` and `prompts/`, `meetings`, `executor`, `approvals`, `deferred_approval`, `watch`, `startup`, `copy_lock`, `budget`, `incidents`, `failures`, `ports` and `state_ports`.

**Ports:** `Clock`, `Tracer`, `LLMClient`, `Mailbox`, `Calendar`, `ApprovalOpener`, `StateStore`, `AuditLog`, `IncidentReporter`, `SpendLedger`, `Lease`.

**Adapters** (`src/aeca/adapters/`):

| Port | Adapter |
|---|---|
| `LLMClient` | `anthropic_client` (retries 429s, 5xx and dropped connections itself, with `max_retries=0` on the SDK), `pricing`, `models_file` |
| `Mailbox` | `gmail` |
| `Calendar` | `google_calendar` |
| Google plumbing | `google_auth`, `google_services`, `google_errors`, `google_seed_calendar`, `google_reset` (reset-test's reads and its two writes) |
| `Lease` | `gmail_lease` (the lock draft), `copy_id_file` |
| Health | `heartbeat_file` |
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
10. **Audit.** One `AuditLine`. Its `rules` hold the rules that fired and the validator's drop notes, such as `urgent: its quote is not in the email`. `AECA/processed` is added last, and `AECA/awaiting` stays while an approval is open.

A failure ends one of two ways ([0036](decisions/0036-two-kinds-of-error.md)). A system-wide one, such as a refused key, a 429, a 5xx or the cost cap, ends the tick, and AECA backs off as a whole. An error in one email is audited as `failed`, the email gets `AECA/error`, and the next polls retry it up to 3 times.

## The approval loop

`ApprovalService.process_all` reads the thread of each open approval.

1. A message counts as an answer only if it has none of AECA's headers and passes one of two checks: Gmail's `SENT` (from the account itself), or `MANAGER_ADDRESS` with a DMARC pass for that same domain. `why_not_an_answer` names the reason when it doesn't, and the reply is audited and ignored.
2. `parse_answer` reads the first word of the first unquoted line.
3. APPROVE: the approval's actions run in order through the executor. On a meeting, the calendar is checked again first, ignoring the approval's own hold. If the slot has filled, the approval is superseded and a new one is sent.
4. REJECT: logged. Nothing else happens ([0030](decisions/0030-reject-does-nothing.md)).
5. An unclear answer gets one help reply.

States: `awaiting → approved → executed | failed`; `awaiting → rejected`; `awaiting → superseded`. Holds are released on REJECT, on supersede, on failure, on execution, and when the slot starts, except that an executed approval that offered alternatives keeps them until each slot starts ([0066](decisions/0066-offered-alternatives-stay-held.md)).

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

## The service loop, the lock and health

`aeca run` sets a stop event on SIGTERM and SIGINT. `Service.run` starts up (`startup`: the checks, then `CopyLock.acquire`, then each action left `executing` is resolved by its key; `pending` ones wait for the tick's ordered resumes), then ticks until the event is set. A tick first calls `ensure_held`, and again before each email and before it records the poll. A lost lock raises `LockLost`, which is not a system-wide error, so the run ends with exit 1.

`domain/lease.py` holds the rule: a heartbeat is fresh for 3 × `POLL_SECONDS`, and one dated further ahead than that is not fresh either. `adapters/gmail_lease.py` stores the lock as the draft `AECA lock`, found with `drafts.list`, with the lowest draft id as the lock. `application/copy_lock.py` renews it on each poll, and between emails once a poll's time has passed. Each renewal also writes the heartbeat file that `aeca health` reads. The copy id comes from `copy-id` in the data directory ([0065](decisions/0065-the-copy-id-is-persisted-in-the-data-dir.md)).

## reset-test

`aeca reset-test` is Ionel's tool, in `tools/reset_test.py` and `tools/reset_plan.py`. It reads every message and the tagged events, applies the rules of `domain/rehearsal_mail.py` (R1 to R4, E1, E2) to one message at a time, and prints a plan. With `--yes` it moves the matched mail to Trash and cancels the matched events: `messages.trash` and `events.delete` are all it writes. `domain/reset_lock.py` makes it refuse while any lock draft may be live, and it checks again before the first write. Why: [0061](decisions/0061-what-reset-test-treats-as-the-rehearsals.md), [0062](decisions/0062-reset-tests-own-lock-check.md).

## The files

| File | Where | What |
|---|---|---|
| `state.db` | `/data` volume | Approvals, holds, the journal, spend, incidents |
| `audit.jsonl` | `/data` volume | One line per email and per approval outcome |
| `trace.jsonl` | `/data` volume | Every step, model call, Google call and retry |
| `heartbeat` | `/data` volume | The time of the last successful poll, for `aeca health` |
| `copy-id` | `/data` volume | This installation's id for the copy lock |
| `debug-YYYY-MM-DD.log` | `/data` volume | With `DEBUG_LOG=true`; rotated, and kept `DEBUG_LOG_DAYS` days. Holds email text and raw model replies |

## The commands

`aeca auth`, `run`, `health`, `audit`, `approvals`, `eval`, `seed-calendar`, `rehearse` and `reset-test`, in `cli.py` and `tools/tool_commands.py`. The `make` targets call them; `make preflight` is `scripts/preflight.sh`.
