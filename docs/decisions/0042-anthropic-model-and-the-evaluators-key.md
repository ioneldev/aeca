# 0042: The model is Anthropic, and the evaluators bring the key

**Who decided:** Ionel. 2026-11-05.

**Decision.**
- The model is Anthropic, not Azure OpenAI. It stays behind a port, so another provider can be added.
- Ionel hands over only the Gmail and Calendar credentials. The evaluators bring their own Anthropic key, and Ionel arranges that with them.
- No fallback is built.

**Why.** The spec gives no reason for choosing Anthropic.

**Rejected.**
- Azure OpenAI, as the brief suggests.
- A fallback key from Ionel, or an Azure OpenAI fallback. Neither is built.

**Cost.** The evaluators must bring an Anthropic key. The brief's hint about substituting their AIS credentials doesn't apply.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §3 and §12.
