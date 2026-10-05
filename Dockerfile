FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The profile builder needs git; WeasyPrint needs Pango and HarfBuzz to render CV PDFs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser job_agent/ job_agent/
COPY --chown=appuser:appuser job_contracts/ job_contracts/
COPY --chown=appuser:appuser config.yaml .
COPY --chown=appuser:appuser resume/base.md resume/base.md

USER appuser
EXPOSE 8080

CMD ["sh", "-c", "exec python -m uvicorn job_agent.webhook:app --host 0.0.0.0 --port ${PORT:-8080}"]
