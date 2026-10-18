# syntax=docker/dockerfile:1
# One image recipe for every Python service in the uv workspace:
#   docker build -f infra/docker/service.Dockerfile \
#     --build-arg PACKAGE=orchestrator-agent --build-arg MODULE=orchestrator .
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services ./services
ARG PACKAGE
# Installs only this service and its dependencies, as regular (non-editable) packages.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package "${PACKAGE}"

FROM ${PYTHON_IMAGE}
ARG MODULE
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    SERVICE_MODULE=${MODULE}
RUN useradd --create-home --uid 10001 app
COPY --from=build /opt/venv /opt/venv
COPY data /app/data
WORKDIR /app
USER app
CMD ["sh", "-c", "exec python -m \"$SERVICE_MODULE\""]
