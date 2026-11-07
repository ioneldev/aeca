#!/bin/sh
# What a Docker make target needs, checked before it starts anything, so a missing tool or setting
# is named with its fix before compose builds or starts a container. Plain sh, because the Docker
# path must work on a machine that has only Docker and make.
#
#   sh scripts/preflight.sh docker     Docker, the Compose plugin, a running daemon, and .env, which
#                                      compose refuses to run without
#   sh scripts/preflight.sh env-auth   .env sets what make auth needs: GMAIL_ADDRESS,
#                                      GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
#   sh scripts/preflight.sh env-run    env-auth, plus GOOGLE_REFRESH_TOKEN and ANTHROPIC_API_KEY
#
# A value is never read out: grep -q prints nothing and no value is held in a variable, so it
# cannot reach the terminal, the environment or a log. Compose hands the values to the container.
set -u

# Tests point this at an env file of their own, so no test reads the real .env.
env_file="${AECA_ENV_FILE:-.env}"

AUTH_VARIABLES="GMAIL_ADDRESS GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET"
RUN_VARIABLES="$AUTH_VARIABLES GOOGLE_REFRESH_TOKEN ANTHROPIC_API_KEY"

not_ready() {
    printf 'preflight: %s\n  next: %s\n' "$1" "$2" >&2
    exit 1
}

check_docker() {
    command -v docker >/dev/null 2>&1 ||
        not_ready "docker is not installed, or not on PATH." \
            "Install Docker Desktop, or Docker Engine with its Compose plugin, then run again."
    docker compose version >/dev/null 2>&1 ||
        not_ready "docker compose is not available." \
            "Install the Docker Compose v2 plugin, which runs as docker compose, then run again."
    docker info >/dev/null 2>&1 ||
        not_ready "Docker is not running, or this user cannot reach it." \
            "Start Docker Desktop or the Docker service, then run again. On Linux, the user must be in the docker group."
    check_env_file
}

check_env_file() {
    [ -f "$env_file" ] ||
        not_ready "$env_file is missing, and docker compose reads the settings from it." \
            "Run cp .env.example .env, fill in the settings, then run again."
}

# A line that sets the variable, bare, quoted or after export. A value that is empty, blank, only
# quotes or a comment does not count.
is_set() {
    grep -Eq "^[[:space:]]*(export[[:space:]]+)?$1=[[:space:]]*[\"']?[^\"'[:space:]#]" "$env_file"
}

check_variables() {
    check_env_file
    missing=""
    for name in $1; do
        is_set "$name" || missing="$missing $name"
    done
    [ -z "$missing" ] ||
        not_ready "$env_file sets no value for:$missing." \
            "Put each in $env_file as NAME=<value> (see .env.example), then run again."
}

for check in "$@"; do
    case "$check" in
        docker) check_docker ;;
        env-auth) check_variables "$AUTH_VARIABLES" ;;
        env-run) check_variables "$RUN_VARIABLES" ;;
        *)
            printf 'preflight: no check named %s.\n  next: Name docker, env-auth, env-run or several.\n' "$check" >&2
            exit 2
            ;;
    esac
done
