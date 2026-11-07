# 0041: Four production limits are named, not built

**Who decided:** Ionel. 2026-11-05.

**Decision.**
The README and `docs/` name these limits and AECA does not address them:
- AECA sends the inbox's contents to Anthropic. In production that needs a data-processing agreement and a decision about where the data is processed.
- Secrets come from `.env`, not from a secret manager.
- AECA polls. At scale it would use Gmail push notifications and the history API.
- AECA serves one inbox per copy.

**Why.** The spec gives no further reason.

**Rejected.**
- Building them within the four days (inferred).

**Cost.** Each is a real gap for production use. That is why they are named.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.12.
