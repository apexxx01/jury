# Jury, for the one-command reproduction in repro/ (and for running Jury anywhere).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jury ./jury
COPY rubrics ./rubrics
COPY templates ./templates
COPY scripts ./scripts
RUN pip install --no-cache-dir "httpx>=0.27" "pyyaml>=6"
ENV PYTHONPATH=/app \
    JURY_STATE_DIR=/data/.jury \
    JURY_WEB_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
CMD ["python", "-m", "jury", "up"]
