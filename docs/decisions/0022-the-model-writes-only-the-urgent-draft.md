# 0022: The model writes one reply only: the urgent draft

**Changed in part by [0059](0059-an-urgent-meeting-gets-one-reply.md) and [0060](0060-the-urgent-draft-goes-out-as-written.md)** (2026-11-07): a meeting request gets no urgent draft, and the draft goes out word for word, with no template greeting or sign-off. What follows is kept as it was written.

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- The acknowledgement, the thank-you and the decline are code templates. The model only decides whether one is warranted, and which kind.
- The one reply the model writes is the urgent internal draft, and the manager reads it before it goes out.
- The draft comes from the decision call, which sees only that one email.
- Code drops a draft that contains a link or an email address, or that runs past 800 characters.
- It goes in the same thread, and carries `Auto-Submitted: auto-replied` and AECA's own headers, so other auto-responders don't answer it.

**Why.** No text written by a model leaves without a human seeing it.

**Rejected.**
- Model-written text for every reply kind (inferred). It would send model text with no human reading it.

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.3.
