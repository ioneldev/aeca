# 0061: What reset-test treats as the rehearsal's

**Refines [0057](0057-rehearsal-through-mail-app.md)'s "identified by its two sender addresses".**

**Who decided:** Claude, 2026-11-07. The test cases come from the `tech-lead` agent.

**Decision.**
- Six rules, each applied to one message, never to a thread:
  - R1: a single `From` equal to a `REHEARSAL_*` address, with `dmarc=pass` for that address's own domain.
  - R2: mail the account itself wrote, with AECA's headers and every recipient a rehearsal address.
  - R3: mail the account itself wrote, with an AECA id equal to an approval id derived from a rehearsal email.
  - R4: a hand reply, sent by the account, whose `In-Reply-To` or `References` names a rehearsal approval request.
  - E1: events tagged by `make seed-calendar`, the whole series.
  - E2: events under ids AECA's executor derives from a rehearsal email.
- DMARC is required, and there is no cutoff date.
- `X-AECA-Test` and the `AECA/test` label are dropped: insert mode was cut ([0048](0048-no-insert-mode-and-no-scorer.md)), and a sender can forge a header.

**Why.** The rehearsal's two senders are Ionel's everyday addresses, and the evaluators' mail may share their threads and subjects. The read-only census of 2026-11-07 found 88 messages outside Trash, all from the two rehearsal addresses (every one `dmarc=pass`) or AECA's mail to them, and 17 + 8 events. The dry run's plan matched it exactly: 88 messages (R1 50, R2 38) and 25 events (E1 17, E2 8).

**Rejected.**
- Matching by thread (Claude; the review's finding: the evaluators' request could sit in a thread shared with the rehearsal).
- A `REHEARSAL_UNTIL` cutoff date (Claude).
- Gmail search (Claude).

**Cost.** A hand reply without `In-Reply-To` or `References` naming the request is left over. It shows only in the count of its sender's "left alone" messages, not as an item.

**Source:** `src/aeca/domain/rehearsal_mail.py`, `src/aeca/tools/reset_test.py`, `src/aeca/tools/reset_plan.py`; [0046](0046-rehearse-with-real-mail-on-the-handover-account.md).
