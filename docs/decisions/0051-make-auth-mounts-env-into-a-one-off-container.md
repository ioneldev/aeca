# 0051: make auth mounts .env into a one-off container

**Who decided:** Claude, in the plan Ionel approved on 2026-11-07.

**Decision.**
- `make auth` bind-mounts `.env` into a one-off container, so the token can be written without being printed.
- This applies only to the `auth` service, which doesn't run AECA.

**Why.** The token has to be written into `.env` without being printed.

**Rejected.**
- Running it on the host with uv. That is the fallback.

**Cost.** It departs from the rule that secrets come only from `env_file`.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md) and [the plan's Deviations](../superpowers/plans/2026-11-07-aeca.md), §4.11.
