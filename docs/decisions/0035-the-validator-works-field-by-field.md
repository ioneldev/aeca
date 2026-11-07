# 0035: The validator drops a bad field, not the whole decision

**Who decided:** Claude, after review; Claude for the urgent-quote check. 2026-11-05.

**Decision.**
- A bad label is the only fault that refuses a whole decision. Only a missing or invalid label goes back to the model, at most twice; after that the email gets `UNKNOWN`.
- Any other fault drops the field it is in, keeps the label, and is noted in the audit line.
- It drops `urgent` on SPAM, and `urgent` whose quote isn't in the email. It drops a window whose quote isn't in the email, a time that doesn't parse, and a time already in the past.
- It drops a reply kind the label's record doesn't allow, and an urgent draft that breaks the rules in 0022.
- A meeting left with no valid window goes down the alternatives path (0013).

**Why.** One bad field never turns a correctly labelled email into `UNKNOWN`. Past times are dropped because the evaluators may write their emails days ahead.

**Rejected.**
- Refusing the whole decision on any fault.

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.7.
