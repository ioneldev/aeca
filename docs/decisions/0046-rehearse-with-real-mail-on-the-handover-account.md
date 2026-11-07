# 0046: The rehearsal sends real mail, on the handover account

**Who decided:** Ionel; Ionel after review for sending real mail; Claude for the reset limits and for `MANAGER_ADDRESS`. 2026-11-05.

**Decision.**
- `make rehearse MODE=send` is the main rehearsal. It sends real mail from the two sender accounts, one listed in `INTERNAL_SENDERS`.
- It runs on the handover account itself, with `MANAGER_ADDRESS` set to an address Ionel controls, as the evaluators will most likely run it.
- `make reset-test` cleans the rehearsal out. It only moves to Trash, touches only what the rehearsal made, refuses while a copy of AECA holds the lock, and shows a dry run first. A second dry run must find nothing left.

**Why.** Only real sending meets Gmail's spam filter, real authentication results and real threading, as the evaluators' mail will. The rehearsal runs on the handover account, so the reset is what leaves it clean. The reset is limited because it is destructive and can't be undone.

**Rejected.**
- `insert` mode as the main rehearsal. It was the first cut (see 0048).
- Deleting instead of moving to Trash.

**Cost.** Gmail won't send as an address the account doesn't own, so cases that need a `noreply` sender or a lookalike domain run only in `make eval` and the offline suite. Gmail empties Trash after 30 days, so the rehearsal stays visible there until then, and Ionel's handover message says so.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §5.
