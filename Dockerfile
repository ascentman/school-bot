FROM python:3.12-slim

# fonts-dejavu-core — кирилиця в PDF-звітах; tzdata — Europe/Kyiv
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Спершу лише маніфести — шар із залежностями кешується між збірками.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app /app
USER app

CMD ["sh", "-c", "alembic upgrade head && school-bot run"]
