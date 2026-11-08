# 0029: Which reply counts as an approval

**Who decided:** Claude for the `SENT` check; Ionel, after review, for the DMARC check. 2026-11-05.

**Decision.**
A reply counts when it is in the request's thread, carries no AECA headers, and passes one of two checks:
- **From the account itself:** the message has Gmail's `SENT` label, which only the account can produce. Whoever holds the account is the manager, so this counts with or without `MANAGER_ADDRESS`.
- **From `MANAGER_ADDRESS`, when it is set:** Gmail's `Authentication-Results` shows DMARC passed for it.
A reply that passes neither check is ignored and audited with the reason. No model reads an approval reply.

**Why.** A `From` alone can be forged. A DMARC pass can't be, without control of that address's domain. `SENT` can't be forged from outside the account.

**Rejected.**
- Trusting the `From` header alone. A spoofed `From` could approve an action; acting on an APPROVE the manager did not write is an anti-goal.

**Cost.** The README says the manager's domain must publish DMARC, as Gmail, Outlook and most companies do. The spec marks the thread and header behaviour to verify.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §2, §4.5 and §11.
