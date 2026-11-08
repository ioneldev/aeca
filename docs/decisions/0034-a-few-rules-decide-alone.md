# 0034: A few named rules decide before the model does

**Who decided:** Claude. 2026-11-05.

**Decision.**
- Some rules decide alone. Each is tested and named in the audit line, and they are kept few.
- The first rule: DMARC fails and a link mismatches, so the email is SPAM.

**Why.** Content decides most of the evaluators' emails, so rules would cover little.

**Rejected.**
- Many rules in front of the model (inferred from "kept few").

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.7.
