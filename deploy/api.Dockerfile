FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/apps/api/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src"

RUN apt-get update \
    && apt-get install --no-install-recommends --yes libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/README.md ./apps/api/
RUN uv sync --project apps/api --frozen --no-dev --no-install-project

COPY apps/api ./apps/api
COPY sql ./sql

RUN uv sync --project apps/api --frozen --no-dev

EXPOSE 8000

CMD ["python", "-m", "voice_shopping_api.server", "--host", "0.0.0.0", "--port", "8000"]
