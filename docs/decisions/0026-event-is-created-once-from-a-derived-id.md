# 0026: An event is created once, from an id derived from the email

**Who decided:** Claude, after review. 2026-11-05.

**Decision.**
- The event id is a hash of the source Gmail message id and an attempt number, encoded in base32hex.
- Before inserting, AECA reads the event by that id. A live event means the work is done. A cancelled one means AECA moves to the next attempt number.

**Why.** Google's docs don't promise to refuse a duplicate id, which is why the read comes first.

**Rejected.**
- Relying on Google to refuse a duplicate id at insert time.

**Cost.** The spec states none. The behaviour is marked to verify on day 1.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.4 and §11.
