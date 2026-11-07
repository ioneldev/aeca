# syntax=docker/dockerfile:1
# AECA in one image: Python 3.12, uv and the locked dependencies. Both compose services run it.

# python:3.12-slim, resolved 2026-11-07. The digest names the multi-platform index, so this line
# builds the same on arm64 and amd64.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
COPY --from=ghcr.io/astral-sh/uv:0.9.27@sha256:143b40f4ab56a780f43377604702107b5a35f83a4453daf1e4be691358718a6a /uv /usr/local/bin/uv

WORKDIR /app
# Output reaches `docker compose logs` and the consent URL reaches the terminal at once.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# The dependencies first, so a change to AECA's own code reuses this layer. The uv cache is a build
# cache, so no layer keeps it.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/uv-cache UV_CACHE_DIR=/uv-cache uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/uv-cache UV_CACHE_DIR=/uv-cache uv sync --frozen --no-dev

# /data is the named volume. It is made and owned here so a fresh volume is writable by aeca.
RUN useradd --uid 1000 --create-home aeca \
 && mkdir -p /data /work \
 && chown aeca:aeca /data /work
ENV PATH=/app/.venv/bin:$PATH \
    AECA_DATA_DIR=/data

USER aeca
ENTRYPOINT ["aeca"]
