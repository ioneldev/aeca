# AECA: guide for agents

AECA, the Agentic Email & Calendar Assistant, watches one Gmail inbox. It labels each new email, takes the action the brief names, and asks the manager by email before any action that needs approval. It is Ionel Cîmpan's take-home for Telekom's Senior Developer Python LLM role. The handover is on Sunday 2026-11-08, in the evening.

## Read first

1. **The design**, which is the authority: `docs/superpowers/specs/2026-11-05-aeca-design.md`.
2. **The plan**, which says how to build it: `docs/superpowers/plans/2026-11-07-aeca.md`. Its "Parallel streams" section says what runs alongside what.
3. **Background:** `/Users/ionel/work/personal/telekom/AGENT_GUIDE.md`, on the patterns carried over from ARDE.
4. **ARDE's code:** `/Users/ionel/work/personal/emag/arde`, for the files the plan says to port. Read it; never change it.

## Rules

- **Secrets.** Never read, print, copy, source, mount or bind `.env`. The one exception is the plan's `auth` Compose service. Never commit a secret, and tell every subagent these rules.
- **Git:**
  - stage files by path, never with `git add .` or `-A`;
  - one commit per task, ending with the session's attribution lines;
  - never push: pushing is Ionel's decision;
  - `main` holds only approved gates, and the build runs on `build`.
- **Tests:**
  - TDD and F.I.R.S.T.;
  - tests that touch the network or cost money carry the `live` or `eval` marker;
  - before writing tests for anything that can't be undone, get the cases from the `tech-lead` agent, and give it the contract and the data, not the intended implementation.
- **Code:** Ionel's clean-code rules apply, as listed under the plan's Global Constraints.
- **Installs:** only the project's own locked dependencies through uv, and the Docker images the plan pins.
- **People:** Ionel's pronouns are they/them.
- **Marking decisions:** mark each one as Ionel's or Claude's. Mark open questions `[NEEDS CLARIFICATION]`, and write departures in a Deviations section.

## Where Ionel is needed

| When | What |
|---|---|
| After Task 1 | Run `make auth` (`! make auth` in a session) |
| Task 2 | Send one email from Proton, and reply APPROVE from the AECA inbox and from Proton |
| Task 8 | Send one email for the first live run |
| Task 12 | Check the corpus's expected answers; create the two sender accounts with app passwords; set `INTERNAL_SENDERS` and `MANAGER_ADDRESS` |
| Tasks 13 and 17 | Approve and reject during the rehearsals |
| Task 18 | The handover checklist |

## The OAuth consent screen

Publishing to "In production" needs a homepage and a privacy-policy link on the Branding page. The plan:
- the homepage is `https://github.com/ioneldev`;
- the privacy policy is a public Gist of `PRIVACY.md`;
- `github.com` is an authorized domain.

If it stays in Testing mode, the token expires 7 days after consent. Ionel then runs `make auth` again just before the handover.
