# 0011: The brief's approval column rules; some replies go out unapproved

**Who decided:** Ionel. 2026-11-05.

**Decision.**
- The approval column of the policy table follows the brief's.
- The sales decline, an acknowledgement and a thank-you go out without approval.
- `SALES_DECLINE_NEEDS_APPROVAL` (default false) makes the decline wait for approval.
- The model decides whether an acknowledgement or a thank-you is warranted.

**Why.** These replies follow the table's approval column, and the brief's line that "the agent decides" when a reply is warranted. The brief's own examples are "a polite acknowledgement to a marketing offer or a brief thank-you where appropriate". The brief also calls the decline's approval "configurable".

**Rejected.**
- Approval before every send, as the brief's requirement reads. It loses to the table's approval column and to "the agent decides".

**Cost.** This contradicts the brief's requirement for "manager approval via email reply before any send". The README states the choice.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2, §4.3 and §12.
