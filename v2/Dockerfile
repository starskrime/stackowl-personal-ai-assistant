FROM python:3.13-slim

WORKDIR /app

# Bring in uv binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies (locked, no dev deps).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Install the stackowl package.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENTRYPOINT ["stackowl"]
