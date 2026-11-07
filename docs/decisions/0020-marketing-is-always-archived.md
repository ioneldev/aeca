# 0020: Every MARKETING email is archived

**Who decided:** Claude; Ionel for the `BULK_SENDERS` list. 2026-11-05.

**Decision.**
- Every MARKETING email is archived, whether or not the sender is a known bulk sender.
- Bulk headers and `BULK_SENDERS` do two jobs: strong evidence for the label, and marking a sender that never gets an automatic reply. They are not a condition for archiving.

**Why.** The brief's row says "newsletter from known bulk sender → archive". The evaluators' emails will probably come from ordinary mailboxes with no bulk headers.

**Rejected.**
- Archiving only known bulk senders, as the brief's row reads. Most of the evaluators' marketing emails would stay in the Inbox.

**Cost.** A departure from the brief's row. The README lists it.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2, §4.10 and §12.
