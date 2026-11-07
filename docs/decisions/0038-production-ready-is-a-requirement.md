# 0038: Production ready is a requirement

**Who decided:** Ionel for the requirement and the list; Claude for the mechanisms. 2026-11-05.

**Decision.**
- It runs unattended: restart policy, a heartbeat the health check reads, and a graceful stop that finishes the email in hand.
- It says when it's broken: one alert email to the manager per incident (cost cap hit, key refused, `ALERT_AFTER_FAILURES` emails failed in a row). The incident state is kept on the volume.
- Logs are usable: JSON on stdout, audit and trace rotate by size, and `debug.log` is off by default.
- CI runs lint, strict typing, the offline suite, the image build and a dependency audit, with no secrets and no calls to Google or Anthropic.
- It is on the never-cut list.

**Why.** Ionel made it a requirement and agreed the list. One alert per incident, not per email, so a restart doesn't send it again. `debug.log` holds full email text, which is personal data, so it is off by default.

**Rejected.**
- One alert per failed email.
- Leaving `debug.log` on by default.

**Cost.** When Gmail itself fails, for example a revoked token, no email can go out. The only signals left are the health check, the logs and a non-zero exit.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §2, §4.12 and §7.
