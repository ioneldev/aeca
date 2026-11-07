# 0017: While INTERNAL_SENDERS is empty, every urgent sender counts as internal

**Who decided:** Ionel, after review. 2026-11-05.

**Decision.**
- With `INTERNAL_SENDERS` empty, an urgent email from anyone gets the internal treatment: the notice, and a draft waiting for approval.
- AECA logs a warning at startup when the list is empty.
- The reply rules still apply, so an automated sender gets the notice but no draft.
- This is a separate record from 0016 because it came from the review, and it is a fallback for a setting, not part of the table's base rule.

**Why.** Evaluators who forget the setting still see the brief's urgent-internal scenario.

**Rejected.**
- Treating everyone as external while the list is empty (inferred). The urgent-internal scenario would then never appear.

**Cost.** Every urgent outsider gets a notice and a draft while the list is empty (inferred). The warning at startup says so.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2, §4.10 and §5.
