# CyberLogix AI — Master Enterprise Hub
# Production container image targeting Google Cloud Run.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY main.py store.py db.py auth.py gemini.py notifications.py costs.py \
     licenses.py accounts.py telemetry.py automation.py voice_dispatch.py \
     forecaster.py hardware_bridge.py console_api.py ./
COPY static/ ./static/

# The SQLite file lives here; mount a volume at /data to outlive the
# container, or leave it ephemeral for a stateless deployment.
ENV CYBERLOGIX_DB_PATH=/app/data/cyberlogix.db
RUN mkdir -p /app/data

# Run as an unprivileged user.
RUN useradd --create-home --shell /usr/sbin/nologin cyberlogix \
    && chown -R cyberlogix:cyberlogix /app
USER cyberlogix

EXPOSE 8080

# Cloud Run injects $PORT; the shell form expands it at container start.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
