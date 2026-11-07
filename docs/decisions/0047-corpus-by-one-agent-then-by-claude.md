# 0047: The test corpus: a generator agent, replaced by Claude writing it

**Who decided:** Ionel for the generator, with Claude after review for running it once, then Ionel for dropping it. 2026-11-05 and 2026-11-07.

**Decision.**
- On 2026-11-05: a separate agent writes a corpus of about 30 emails that replicates the evaluators' test. It runs once, never sees AECA's prompt, and uses a different model from the decision call. The committed corpus is what matters.
- On 2026-11-07: there is no generator agent. Claude writes the corpus, and Ionel checks the expected answers once.

**Why.** The corpus should not share AECA's own assumptions. It is a script run once because the committed corpus matters, not a tool to maintain. The 2026-11-07 change gives no further reason in the plan.

**Rejected.**
- A generator that is maintained as a tool (the spec names this as rejected).
- The generator itself, on 2026-11-07.

**Cost.** The corpus shares Claude's assumptions about what the evaluators will send. Ionel's check is the only independent view of it.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md) and [the plan's Deviations](../superpowers/plans/2026-11-07-aeca.md), §5.
