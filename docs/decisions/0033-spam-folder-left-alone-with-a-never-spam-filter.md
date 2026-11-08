# 0033: Gmail's Spam folder is left alone, and a filter keeps mail out of it

**Who decided:** Ionel; the filter after review; Claude for creating it by hand. 2026-11-05.

**Decision.**
- AECA does not read Gmail's Spam folder. Mail Gmail filtered there gets no audit line, and the README says so.
- A Gmail filter matches every message and is set to "Never send it to Spam". AECA still moves phishing to Spam itself.
- Ionel creates the filter by hand on day 1. The README tells anyone installing AECA on another account to do the same.

**Why.** AECA watches only the Inbox. Without the filter, Gmail could file the evaluators' phishing email in Spam before AECA sees it, leaving no label and no audit line. Creating the filter by hand needs no extra OAuth scope.

**Rejected.**
- Reading the Spam folder. It is a non-goal; the spec gives no further reason.
- Creating the filter through the API. It needs an extra OAuth scope.

**Cost.** "Every processed email" holds, but not every email that arrives is processed. With the filter, the gap should be only mail Gmail judges dangerous enough to override it.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §2, §4.6, §11 and §12.
