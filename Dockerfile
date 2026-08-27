# Socket Mode worker: holds an outbound websocket to Slack, serves no HTTP.
# Runs as-is on ECS Fargate, GCE/EC2 (via docker or systemd), Lightsail, and
# Cloud Run worker pools. See the Deployment section of the README.
FROM python:3.12-slim

# PYTHONUNBUFFERED so logs reach CloudWatch / Cloud Logging as they happen
# rather than sitting in a buffer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so code edits don't invalidate the install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY scout/ ./scout/
# The resume the Resume Parser reads. Baked in, so keep the image in a private
# registry — or mount data/ as a volume and drop this line.
COPY data/ ./data/

# Don't run as root, and let the app write logs/.
RUN useradd --create-home --uid 1000 scout \
    && mkdir -p /app/logs \
    && chown -R scout:scout /app
USER scout

# No Ollama in the container, so there is nothing to fall back to: an empty
# value disables the retry instead of making every failure fail twice.
ENV FALLBACK_BACKEND=""

# Exec form, so the process is PID 1 and receives SIGTERM directly on stop.
CMD ["python", "run.py"]
