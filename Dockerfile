FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY README.md ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --locked

RUN addgroup --system invoiceops && \
    adduser --system --ingroup invoiceops invoiceops && \
    mkdir -p /app/var && \
    chown -R invoiceops:invoiceops /app

USER invoiceops

EXPOSE 8000

CMD ["uvicorn", "invoiceops.legacy.app:app", "--host", "0.0.0.0", "--port", "8000"]
