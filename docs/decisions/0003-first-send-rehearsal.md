# 0003: The first `send` rehearsal

**Changed in part by [0061](0061-what-reset-test-treats-as-the-rehearsals.md)** (2026-11-07): reset-test finds rehearsal mail by more than its two sender addresses. What follows is kept as it was written.

- **Run on:** 2026-11-07, 16:04–16:11 UTC, against `aecatest74@gmail.com`, by Claude, with Ionel's go.
- **Build:** `build` at `bbd97ba`. That covers Task 11's full inbox loop, with the image built at `019e6e7`, and the Mail.app rehearsal sender. Approval requests aren't sent yet. Until Task 13, an approval is saved as `awaiting` and the email gets `AECA/awaiting`.
- **Model:** `claude-opus-5-5` at effort `low`.
- **Senders:** Mail.app, decided by Ionel instead of new Gmail accounts.
  - Internal role: `ionel.cimpan@icloud.com`, which is listed in `INTERNAL_SENDERS`.
  - Every other role: `ionel.cimpan@protonmail.com`, which is also `MANAGER_ADDRESS`.

## Steps

1. `make seed-calendar` created 17 tagged events in the week of 2026-11-09, in Europe/Bucharest.
   - Read back: Monday 13:00 is *declined* by the account (Google kept the declined self-attendee), and Friday 15:00 is *tentative*.
2. One transport-check email went through Mail.app.
   - The first attempt failed safely, before anything was sent. Mail.app's `accounts whose email addresses contains …` filter returns nothing, so it was replaced by a loop over the accounts (`bbd97ba`).
   - The second attempt arrived in the Inbox from the Proton address, with its PDF attached and `dmarc=pass`.
3. `make start` started AECA. On start, the state file migrated from schema version 1 to version 2.
4. `make rehearse` sent 27 of the 35 corpus emails, 2 s apart.
   - The other 8 run in `make eval` only: 3 a real account can't send, and 5 that need bulk headers or HTML, which Mail.app can't produce.
5. The results were checked with `make audit`, the Gmail labels, the Spam folder and the journal, then `make stop`.

## Results (measured)

| Measure | Result |
|---|---|
| Emails processed | 27 of 27: 16 `done`, 11 `awaiting_approval`, 0 failed, all decided by the model |
| Time | first audit line 16:06:00, last 16:09:01 UTC: 27 emails in about 3 minutes (target: 20 within 5) |
| Cost | $0.159 (97,822 tokens in, 5,172 out) |
| Label, urgency and approval match the corpus | 26 of 27 |
| Unprocessed mail left in the Inbox | 0 |
| Mail Gmail filed in Spam on its own | 0. The three messages in Spam were all moved by AECA. |
| Thread siblings (§5 blocking case 7) | Archiving the sales "Quick question" left its same-thread task sibling in the Inbox |
| One unapproved reply per thread | The sales follow-up in the same thread got no second decline, because the thread already had one |

## Where AECA differed from the corpus, and why

- **`invoice-with-injected-instruction` was labelled SPAM; the corpus expects INFO_ONLY.**
  - The model's summary: "Claims to be a Delta Print Co invoice… It came from a personal protonmail address".
  - This comes from the rehearsal itself: every outside email is sent from Ionel's personal Proton address. `make eval`, which uses a neutral sender, labels it INFO_ONLY.
- **`meeting-urgent-internal-escalation` got alternatives instead of the free Monday 14:00 slot.**
  - An earlier corpus email (Priya's) had already proposed Monday 14:00, and its approval holds that slot.
  - Holds keep two requesters apart (spec §4.4), so the second requester correctly sees the slot as busy.
  - The corpus marked the slot "free" against the seeded calendar alone.
- **`meeting-no-time-proposed` ("sometime next week") got an event at Monday 13:10, for approval, instead of alternatives.**
  - The model read "next week" as a window, and the spec's rule ("the earliest free slot in the first window that has one") booked the first free half hour after the other threads' holds.
  - This follows the spec. Whether a vague request should get three alternatives instead is a question for Ionel's corpus check.
  - **[NEEDS CLARIFICATION: vague window → earliest slot, or alternatives?]** Resolved by [0058](0058-vague-requests-get-alternatives.md): alternatives.
- **The acknowledgement and thank-you choices varied.** The model decides whether one is warranted (spec §4.3).
  - Promotion: no acknowledgement.
  - Task with a far deadline: acknowledgement.
  - Urgent external task: acknowledgement.
  - First of a pair in one thread: no acknowledgement. Its follow-up: acknowledgement.
  - Never more than one unapproved reply per thread.
- **Notation, not behaviour.** AECA applies `URGENT` and `Finance` as separate label actions, and SPAM has no user label, because Gmail refuses one. The corpus counts these differently.

## Not settled by this run

- **Spike 13 (whether Gmail files a sender's later mail in Spam after AECA moves one of its emails):** not settled. Mail.app sent every email within a minute, before AECA moved the spam.
- **Spike 17 (the never-Spam filter):** not checked.
- **Spike 12 (matching an invitation by its iCalUID):** no invitation was sent.

## Approvals answered live (2026-11-07)

Task 13's live check, with the approval loop in place. Ionel answered from the manager's Proton address.

- 11 approval requests went to the manager's Proton address, and 10 were answered:
  - 4 approvals created the event and sent the confirmation.
  - 4 approvals sent the alternatives.
  - The urgent task's reply was sent. It still used the old wrapped template, because the running image predated [0060](0060-the-urgent-draft-goes-out-as-written.md).
  - One unclear answer got a help reply, and the REJECT that followed was recorded and released its holds.
- 5 more meeting requests sent later were approved and executed.
- 8 live AECA events match the 8 executed `create_event` actions.
- The one left awaiting is Dana's urgent meeting from before the change ([0059](0059-an-urgent-meeting-gets-one-reply.md)). The reset cleans it up.

## Follow-ups

- **Ionel's corpus check (Task 12 Step 5):** the vague-window question above, and the order-dependent Monday 14:00 slot shared by two corpus emails.
- **Cleanup:** `make reset-test` ([0061](0061-what-reset-test-treats-as-the-rehearsals.md)) moves this run's mail to Trash and cancels the seeded events. Rehearsal mail is identified by its two sender addresses.
