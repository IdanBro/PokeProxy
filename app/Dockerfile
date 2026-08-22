# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/pokeproxy/venv

WORKDIR /build

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=/build/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/build/uv.lock \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


FROM ${PYTHON_IMAGE} AS runtime

ARG GIT_SHA=unknown

LABEL org.opencontainers.image.title="pokeproxy" \
      org.opencontainers.image.description="Pokemon stream reverse proxy" \
      org.opencontainers.image.revision="${GIT_SHA}"

ENV PATH=/opt/pokeproxy/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POKEPROXY_CONFIG=/etc/pokeproxy/rules.json

RUN groupadd --system --gid 10001 pokeproxy \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin pokeproxy

COPY --from=builder /opt/pokeproxy/venv /opt/pokeproxy/venv

WORKDIR /app
USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "pokeproxy"]
