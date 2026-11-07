# 0027: Approval ids and AECA's headers survive a lost state store

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- An approval's id is derived from the source Gmail message id, never a counter.
- Every message AECA sends carries `X-AECA-Kind` and `X-AECA-Id`.
- AECA recognises its own mail by these headers together with `SENT`. It recognises an approval thread by that thread's first message.
- Neither check depends on the state store.

**Why.** A fresh state store can never reuse an id. A lost volume can't make AECA mistake its own request for the manager's answer.

**Rejected.**
- A counter for ids. It can repeat after a wipe.
- Recognising its own mail from the state store.

**Cost.** The headers must survive sending, which the spec marks to verify. Gmail may rewrite a custom Message-ID, so AECA doesn't rely on one.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.5 and §11.
