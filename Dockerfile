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
# constraints.txt pins every package, including the ones requirements.txt
# pulls in, so the image installs exactly what CI tested.
COPY requirements.txt constraints.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt -c constraints.txt

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

# The release number and its changelog must be in the image: /api/health
# and the console's About card read VERSION, and a build that dropped
# either (a .dockerignore rule, say) fails here rather than in production.
RUN test -s /app/VERSION && test -s /app/CHANGELOG.md

# SQLite lives here. Mount a volume at /app/data to outlive the container;
# leave it unmounted for a stateless deployment that rebuilds from
# telemetry. Note the one-process assumption below.
ENV CYBERLOGIX_DB_PATH=/app/data/cyberlogix.db

RUN mkdir -p /app/data \
    && useradd --create-home --shell /usr/sbin/nologin cyberlogix \
    && chown -R cyberlogix:cyberlogix /app \
    && chmod 755 /app/docker-entrypoint.sh

EXPOSE 8080

# The container starts as root for one reason: a host's disk is often mounted
# owned by root, and the app could not create its database on it. The
# entrypoint gives the data folders to `cyberlogix` and then drops to that
# user with setpriv, before the application starts. Nothing else runs as root.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# One worker on purpose. Records are held in memory and the unattended sweep
# runs in-process, so a second worker would keep its own copy of everything
# and escalate the same incident twice. CYBERLOGIX_SWEEP_SECONDS=0 with an
# external scheduler driving POST /api/autopilot/sweep-all changes who starts
# the sweep, not how many processes are safe. See DEPLOY.md, section 2.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
