# 0021: The policy is data: one record per label

**Who decided:** Claude, from Ionel's future idea. 2026-11-05.

**Decision.**
- Each label is one record: its name, description and examples, the decision fields it needs, the reply kinds it allows, and its steps in order.
- A step is `{when, verb, args, approval}`. `when` names a predicate from a fixed set. `verb` names one of a fixed set of verbs. `approval` is `none`, `required` or `setting:<NAME>`.
- `URGENT` is a modifier record of the same shape.
- The prompt's label section, the list of allowed labels and the validator's rules are all generated from the records.
- A test checks that every predicate and verb a record names exists.
- A record combines verbs; it can't add one. The verbs that take an email out of the Inbox, archiving and moving to Spam, always run last.
- Version 1 keeps the records in code, with no loader and no expression language.

**Why.** Ionel's future idea is rules a user can edit, and new labels defined in a structured way. Records make that a small step: a YAML loader can reuse the same test later.

**Rejected.**
- A loader and an expression language in version 1.
- Policy scattered through the code as branches (inferred).

**Cost.** In version 1 nobody can edit the rules without changing code.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.2 and §8.
