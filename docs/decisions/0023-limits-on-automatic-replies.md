# 0023: Code limits who gets an automatic reply

**Who decided:** Claude. 2026-11-05.

**Decision.**
- The recipient is the original `From` address. `Reply-To` is ignored.
- Never to SPAM, never to the account itself, never to a `From` whose DMARC check failed.
- Never to an automated sender (RFC 3834): `Auto-Submitted` set to anything but `no`; `Precedence` of `bulk`, `list` or `junk`; `List-Id` or `List-Unsubscribe`; a sender on `BULK_SENDERS`; or a local part, after any `+tag`, of `noreply`, `no-reply`, `mailer-daemon` or `postmaster`, or (after the final review) `do-not-reply`, `donotreply`, or one ending in `-noreply`, `-no-reply` or `_noreply`.
- At most one reply per thread sent without approval. Approved replies, such as a confirmation, don't count.

**Why.** Phishing uses `Reply-To` to redirect replies. The automated-sender list follows RFC 3834. A reply loop with another auto-responder is one of the anti-goals.

**Rejected.**
- Honouring `Reply-To` (it redirects replies; see above).
- No per-thread limit (inferred). Two auto-responders could keep answering each other.

**Cost.** The spec states none. An out-of-office with no auto-reply headers still gets one reply at most.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §2, §4.3 and §5.
