.PHONY: check preflight image auth eval seed-calendar rehearse

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

# Test tools. Each checks .env first, then runs with its values; none starts Docker.
#   make eval           PAID: decides every corpus email for real, and reports accuracy and cost
#   make seed-calendar  fills next week of the real calendar with events tagged [AECA test]
#   make rehearse       sends the deliverable corpus emails from the two rehearsal accounts
eval:
	@$(PREFLIGHT) env-run
	uv run --env-file .env aeca eval

seed-calendar:
	@$(PREFLIGHT) env-run
	uv run --env-file .env aeca seed-calendar

rehearse:
	@$(PREFLIGHT) env-run
	uv run --env-file .env aeca rehearse
