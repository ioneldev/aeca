# 0060: The urgent draft goes out as the manager read it

**Who decided:** Ionel found the problem, and Claude chose the fix (2026-11-07).

**Decision.**
- The urgent reply is the validated draft, word for word: no template greeting before it and no sign-off after it.
- The draft keeps its subject and threading, its `Auto-Submitted` and AECA headers, and the same handling of a line that starts with an answer word as every other reply.
- The model writes the draft in the manager's voice and leaves it unsigned.

**Why.** The template wrapped the model's draft in its own "Hello," and "Kind regards, AECA". The draft already had its own greeting and sign-off, so the sender would have read two of each. The manager approves the text they see, so the text that goes out has to be that text.

**Rejected.**
- Stripping the model's greeting and sign-off and keeping the template's (Claude). It changes the text after the manager has read it.

**Cost.** None found. A draft that comes back without a greeting goes out without one.

**Source:** commit `4bf7fb0`; `urgent_reply` in `src/aeca/domain/templates.py`; the prompt in `src/aeca/application/prompts/decision.md`.
