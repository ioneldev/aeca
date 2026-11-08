"""What the Compose file, the Makefile and CI promise: unattended running, a safe shutdown, and
nothing fetched unpinned."""

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())
MAKEFILE = (REPO_ROOT / "Makefile").read_text()
CI_WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
SERVICE: dict[str, Any] = COMPOSE["services"]["aeca"]


def recipe_of(target: str) -> list[str]:
    block = re.search(rf"^{target}:.*\n((?:\t.*\n?)*)", MAKEFILE, re.MULTILINE)
    assert block is not None, f"no {target} target"
    return [line.strip() for line in block.group(1).splitlines()]


def test_the_service_restarts_unless_stopped() -> None:
    assert SERVICE["restart"] == "unless-stopped"


def test_the_grace_period_covers_three_model_calls_and_the_google_calls_after_them() -> None:
    assert SERVICE["stop_grace_period"] == "120s"


def test_the_proxy_and_ca_settings_pass_through_by_interpolation_never_by_value() -> None:
    assert SERVICE["environment"] == {
        "HTTPS_PROXY": "${HTTPS_PROXY:-}",
        "EXTRA_CA_CERTS": "${EXTRA_CA_CERTS:-}",
    }


def test_every_docker_target_checks_docker_and_the_settings_first() -> None:
    """Spec §4.11: without Docker, the user gets the next step, not Docker's own error."""
    for target in ("preflight", "start", "stop", "logs", "audit", "approvals"):
        recipe = recipe_of(target)
        assert "$(PREFLIGHT) docker env-run" in recipe[0], target


def test_every_tool_ci_fetches_with_uvx_is_pinned_to_a_version() -> None:
    """AGENTS.md allows only locked dependencies; a tool fetched on the fly is pinned instead."""
    tools = re.findall(r"\buvx\s+(\S+)", CI_WORKFLOW)

    assert tools
    assert all(re.fullmatch(r"[\w.-]+==[\w.]+", tool) for tool in tools), tools


def test_the_targets_run_the_documented_compose_commands() -> None:
    assert "docker compose up -d aeca" in recipe_of("start")[-1]
    assert "docker compose stop aeca" in recipe_of("stop")[-1]
    assert "docker compose logs -f aeca" in recipe_of("logs")[-1]
    assert "docker compose run --rm --no-deps aeca audit" in recipe_of("audit")[-1]
    assert "docker compose run --rm --no-deps aeca approvals" in recipe_of("approvals")[-1]
