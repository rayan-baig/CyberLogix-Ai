# CyberLogix AI — Master Enterprise Hub
#
# Production image, targeting Cloud Run but plain enough for anything that
# runs a container.
#
# Built in two stages so the wheel build tooling never reaches the running
# image: what ships is the interpreter, the installed packages and the
# application, and nothing that could compile code at runtime.

# ---------- build ----------
FROM python:3.11-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080

COPY --from=build /opt/venv /opt/venv

WORKDIR /app

# Copied as a whole rather than module by module. The previous list had
# fallen thirteen modules behind the application and the image would have
# crashed on import — an explicit manifest is only safe if something
# fails when it drifts, and nothing did. .dockerignore is what keeps the
# tests, the local database and the virtualenv out.
COPY . .

# SQLite lives here. Mount a volume at /app/data to outlive the container;
# leave it unmounted for a stateless deployment that rebuilds from
# telemetry. Note the scheduler assumption below before scaling out.
ENV CYBERLOGIX_DB_PATH=/app/data/cyberlogix.db

RUN mkdir -p /app/data \
    && useradd --create-home --shell /usr/sbin/nologin cyberlogix \
    && chown -R cyberlogix:cyberlogix /app
USER cyberlogix

EXPOSE 8080

# One worker on purpose. The unattended sweep runs in-process, so a second
# worker would escalate the same incident twice; scale out by setting
# CYBERLOGIX_SWEEP_SECONDS=0 and driving POST /api/autopilot/sweep from an
# external scheduler instead.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
