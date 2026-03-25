FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN python -m venv .venv && \
    uv sync --frozen --no-dev --no-build-isolation

COPY src ./src

EXPOSE 9898

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "src.server.app:app", "--host", "0.0.0.0", "--port", "9898"]
