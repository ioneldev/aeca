# 0032: AECA skips its own mail and approval threads

**Who decided:** Claude. 2026-11-05.

**Decision.**
- AECA skips its own messages and every approval thread. The approval loop reads only approval threads.
- AECA labels its own messages, and each approval reply once read, `AECA/processed`.
- Any other mail the account sends to itself is classified like any email.

**Why.** Labelling them stops the inbox query returning them on every poll. Self-sent mail is classified because the evaluators may send test mail from the account itself.

**Rejected.**
- Skipping every message from the account itself. It would ignore test mail sent from the AECA account (inferred from the reason).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.6.
