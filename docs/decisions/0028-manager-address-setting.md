# 0028: MANAGER_ADDRESS: approvals can go to the evaluators' own mailbox

**Who decided:** Ionel, after review. 2026-11-05.

**Decision.**
- Approval requests, urgent notices and alerts go to `MANAGER_ADDRESS` when it is set, otherwise to the AECA account itself.
- The README tells evaluators to set it to their own address and approve from there, or leave it empty and approve from the AECA inbox.

**Why.** With it set, evaluators approve from their own mailbox and never need to log in to the AECA account, where Google may challenge a login from a new device.

**Rejected.**
- Approval only from the AECA account. It forces evaluators through a login Google may challenge.

**Cost.** A reply from `MANAGER_ADDRESS` is trusted only by a DMARC check (see 0029).

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.5, §4.10 and §10.
