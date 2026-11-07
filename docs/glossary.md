# Glossary

Terms as AECA uses them.

**Action**: One step of a plan, with a verb, its args and a key. Verbs: `apply_label`, `archive`, `move_to_spam`, `star`, `send_reply`, `notify_manager`, `request_approval`, `create_event`. A policy record combines verbs; it can't add one.

**Alert**: One email to the manager when something is broken: the cost cap, a refused key, or `ALERT_AFTER_FAILURES` failures in a row. One per incident kind per UTC day.

**Alternatives**: Up to three free slots offered in a drafted reply when no proposed time is free, none was proposed, or the window was vague ([0013](decisions/0013-no-free-slot-drafts-alternatives.md), [0058](decisions/0058-vague-requests-get-alternatives.md)).

**AECA headers**: `X-AECA-Kind` and `X-AECA-Id` on every message AECA sends. With Gmail's `SENT` label they identify AECA's own mail ([0027](decisions/0027-approval-ids-and-headers-need-no-state-store.md)).

**Answer word**: `APPROVE`, `APPROVED`, `REJECT` or `REJECTED`, on the first unquoted line of a reply that starts with one.

**Approval**: What the manager is asked to approve: an ordered list of actions, each with its own status and key. States: `awaiting`, `approved`, `executed`, `failed`, `rejected`, `superseded`. Its id derives from the source Gmail message id.

**Approval loop**: The part of each tick that reads answers to open approvals. It runs before the inbox loop.

**Audit line**: One JSON line in `audit.jsonl` per processed email, and one per approval outcome. No bodies.

**Decision**: What the model returns for one email: label, urgent with a quote, finance, meeting windows, reply kind, urgent draft, summary, suspected injection. Code validates it.

**Derived id**: An id computed from the source message id, so a fresh state store can never reuse one: the approval id, the event id, the action keys.

**Facts**: What code extracts from an email before any model call: sender, DMARC, bulk and automated signs, links, attachments, bounded text.

**Hold**: A slot in an unanswered approval, or one of the alternatives offered to a requester. Keyed by thread. Other threads see it as busy.

**Internal sender**: An address in `INTERNAL_SENDERS` whose DMARC check didn't fail. While the list is empty, every urgent sender counts as internal ([0017](decisions/0017-empty-internal-senders-means-everyone-is-internal.md)).

**Journal**: The stored decision and planned actions of one email, written before the first side effect and marked as each action finishes. Restarts resume from it.

**Label record**: The policy for one label as data: description, examples, allowed reply kinds and ordered steps.

**Manager**: Whoever answers approvals: the owner of the AECA account, or `MANAGER_ADDRESS`.

**Never-Spam filter**: A Gmail filter matching every message, set to "Never send it to Spam", so AECA sees mail Gmail would hide.

**Port**: An interface in the application layer (`Mailbox`, `Calendar`, `LLMClient`, `StateStore`, `AuditLog`, `Clock`, `Tracer`). An adapter implements it.

**Rehearsal**: `make rehearse`: the corpus sent as real mail through Mail.app, from two addresses, to the handover account ([0057](decisions/0057-rehearsal-through-mail-app.md)).

**Rule**: A named, tested check that decides a label before the model does.

**Supersede**: An approval closed because its slot filled. A new approval with the next free slots replaces it.

**Tick**: One poll. The approval loop runs, then the inbox loop, one email at a time, oldest first.

**Urgent modifier**: A second policy record added to a primary label: star, the `URGENT` label, and for an internal sender a notice and a drafted reply.

**Vague window**: A proposed time range wider than 12 hours. It isn't a proposed time.
