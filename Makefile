.PHONY: check preflight image auth start stop logs audit approvals eval seed-calendar rehearse reset-test

# What CI and a reviewer run. Tests marked live or eval touch the network or cost money, so they
# never run here.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --strict src
	uv run pytest -m "not live and not eval" -q

# The Docker path. Compose reads .env, and each target first checks what it needs. The check only
# asks whether .env sets a value: it never reads one out.
#   make preflight   check Docker, Compose, the daemon, .env and every setting a run needs
#   make image       build the one image that both compose services use
#   make auth        one-time Google consent in the browser; writes the refresh token into .env
#   make start       run AECA in the background; make stop stops it, after the email in hand
#   make logs        follow the per-email summary lines
#   make audit       print the audit log
#   make approvals   list the approvals the manager has not answered yet
PREFLIGHT = sh scripts/preflight.sh
IMAGE = aeca:local

preflight:
	@$(PREFLIGHT) docker env-run
	@echo "preflight: Docker, Compose, the daemon, .env and every setting are ready."

image:
	@$(PREFLIGHT) docker
	docker compose build

# The token does not exist yet, so only the settings consent needs are checked. The image is built
# when it is missing, so a first run needs no separate make image.
auth:
	@$(PREFLIGHT) docker env-auth
	@docker image inspect $(IMAGE) >/dev/null 2>&1 || docker compose build
	docker compose run --rm --service-ports auth

start:
	@$(PREFLIGHT) env-run
	docker compose up -d aeca

stop:
	@$(PREFLIGHT) env-run
	docker compose stop aeca

logs:
	@$(PREFLIGHT) env-run
	docker compose logs -f aeca

# --no-deps and run, not exec, so the audit prints even when the service is stopped.
audit:
	@$(PREFLIGHT) env-run
	docker compose run --rm --no-deps aeca audit

# Reads the state file on the volume, so it works whether or not the service is running.
approvals:
	@$(PREFLIGHT) env-run
	docker compose run --rm --no-deps aeca approvals

# Test tools. Each checks .env first, then runs with its values; none starts Docker. They run on
# the host, so their files go to ./data, which git ignores, not to the container's /data.
#   make eval           PAID: decides every corpus email for real, and reports accuracy and cost
#   make seed-calendar  fills next week of the real calendar with events tagged [AECA test]
#   make rehearse       sends the plain deliverable corpus emails through Mail.app, from the two rehearsal addresses
#   make reset-test     shows what the rehearsal left in the account; ARGS=--yes moves its mail to Trash
#                       and cancels its events
eval:
	@$(PREFLIGHT) env-run
	AECA_DATA_DIR=data uv run --env-file .env aeca eval

seed-calendar:
	@$(PREFLIGHT) env-run
	AECA_DATA_DIR=data uv run --env-file .env aeca seed-calendar

rehearse:
	@$(PREFLIGHT) env-run
	AECA_DATA_DIR=data uv run --env-file .env aeca rehearse

reset-test:
	@$(PREFLIGHT) env-run
	AECA_DATA_DIR=data uv run --env-file .env aeca reset-test $(ARGS)
