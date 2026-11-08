# 0057: The rehearsal sends through Mail.app, from two addresses it already has

**Changed in part by [0061](0061-what-reset-test-treats-as-the-rehearsals.md)** (2026-11-07): reset-test finds rehearsal mail by more than its two sender addresses. What follows is kept as it was written.

**Who decided:** Ionel. 2026-11-07. This supersedes [0049](0049-the-rehearsal-sends-over-smtp.md).

**Decision.**
- `make rehearse` sends the corpus through macOS Mail.app, driven by AppleScript, from two addresses Mail.app already has: `REHEARSAL_INTERNAL_SENDER` and `REHEARSAL_EXTERNAL_SENDER`.
- No new Gmail accounts are made, and there are no app passwords.
- Mail.app can set neither custom headers nor HTML. So 27 of the 32 deliverable corpus emails are rehearsed live. The other 5 run only in `make eval`, as do the 3 that a real account can't send at all (a `noreply` sender, a lookalike domain).
- Rehearsal mail is told apart by its sender address, so `make reset-test` finds it by those two addresses.

**Why.** Ionel chose to use accounts already on the Mac instead of making two more Gmail accounts and setting up 2-Step Verification and app passwords on each. It is the quickest way to real mail with real authentication results and real threading. The first rehearsal ran this way: 27 emails in about 3 minutes, for $0.16 ([0003](0003-first-send-rehearsal.md)).

**Rejected.**
- SMTP with app passwords from two new Gmail accounts, which is what [0049](0049-the-rehearsal-sends-over-smtp.md) and the plan said. It needs two new accounts, each with 2-Step Verification and an app password.
- The Gmail API for the sender accounts, which needs an OAuth consent for each (0049's reason).

**Cost.**
- The rehearsal runs only on a Mac with Mail.app, signed in to both addresses.
- Eight corpus emails are never sent live. Their expected answers are checked only by `make eval`.
- Because every outside email comes from one personal address, a phishing-looking email can be labelled SPAM by the model for the sender's sake, where `make eval` labels it INFO_ONLY ([0003](0003-first-send-rehearsal.md)).
- The addresses are the ones Ionel uses every day, so `make reset-test` must keep to mail from and to them that the rehearsal made.

**Source:** Ionel's message to the controller, 2026-11-07; [0003](0003-first-send-rehearsal.md); `src/aeca/tools/rehearse.py`.
