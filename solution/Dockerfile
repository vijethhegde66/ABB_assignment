# One image for all Python services (simulator, MCP servers, RAG ingestion, copilot backend).
# The command is selected per service in docker-compose.yml.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/libs:/app/apps/simulator:/app/connectors:/app/mcp-servers/alarm-management:/app/mcp-servers/work-orders:/app/apps/backend:/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY libs ./libs
COPY apps/simulator ./apps/simulator
COPY connectors ./connectors
COPY mcp-servers ./mcp-servers
COPY rag ./rag
COPY apps/backend ./apps/backend
COPY apps/frontend ./apps/frontend

# Non-root runtime user; the RAG index lives on a writable volume.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/var && chown -R app:app /app/var
USER app

EXPOSE 8000 8080 9000 9100
CMD ["python", "-m", "copilot"]
