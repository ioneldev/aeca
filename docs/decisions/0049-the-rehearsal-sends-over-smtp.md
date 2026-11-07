# 0049: The rehearsal sends over SMTP with app passwords

**Who decided:** Claude. 2026-11-07.

**Decision.**
The rehearsal sends its test mail over SMTP with app passwords, not through the Gmail API.

**Why.** It saves an OAuth consent for each sender account.

**Rejected.**
- The Gmail API for the sender accounts. Each would need its own consent.

**Cost.** Each sender account needs 2-Step Verification and an app password.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md) and [the plan's Deviations](../superpowers/plans/2026-11-07-aeca.md), §5.
