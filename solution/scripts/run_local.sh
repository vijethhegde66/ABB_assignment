#!/usr/bin/env bash
# Start the full stack locally without Docker (simulator, MCP servers, copilot backend + GUI).
# Usage: scripts/run_local.sh            (Ctrl+C stops everything)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
export PYTHONPATH="libs:apps/simulator:connectors:mcp-servers/alarm-management:mcp-servers/work-orders:apps/backend:."
if [[ -f .env ]]; then set -a; source .env; set +a; fi

# Local defaults (override in .env). Secrets here are local-development placeholders only.
export SIM_API_TOKENS="${SIM_API_TOKENS:-${ALARM_API_TOKEN:-local-dev-token}}"
export ALARM_API_TOKEN="${ALARM_API_TOKEN:-local-dev-token}"
export ALARM_API_BASE_URL="${ALARM_API_BASE_URL:-http://127.0.0.1:8000}"
export MCP_AUTH_TOKEN="${MCP_AUTH_TOKEN:-local-mcp-token}"
export MCP_SERVER_URL="${MCP_SERVER_URL:-http://127.0.0.1:9000/mcp}"
export WO_MCP_AUTH_TOKEN="${WO_MCP_AUTH_TOKEN:-local-wo-token}"
export WORK_ORDERS_MCP_TOKEN="${WORK_ORDERS_MCP_TOKEN:-$WO_MCP_AUTH_TOKEN}"
export WO_MCP_SIGNING_KEY="${WO_MCP_SIGNING_KEY:-local-dev-signing-key}"
export WORK_ORDERS_MCP_URL="${WORK_ORDERS_MCP_URL:-http://127.0.0.1:9100/mcp}"
export ENABLE_DEMO_CONTROLS="${ENABLE_DEMO_CONTROLS:-true}"
mkdir -p var/logs

"$PY" -m rag.ingestion.cli --docs "${DOCUMENT_PATH:-rag/documents}" --index "${RAG_INDEX_PATH:-var/rag-index}" > var/logs/ingest.json

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

SIM_PORT=8000 "$PY" -m alarm_simulator 2> var/logs/simulator.log & pids+=($!)
MCP_PORT=9000 "$PY" -m alarm_mcp 2> var/logs/alarm-mcp.log & pids+=($!)
WO_MCP_PORT=9100 WO_MCP_AUTH_TOKEN="$WO_MCP_AUTH_TOKEN" "$PY" -m work_orders_mcp 2> var/logs/work-orders-mcp.log & pids+=($!)

wait_for() { for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 0.5; done; echo "timeout waiting for $1" >&2; return 1; }
wait_for http://127.0.0.1:8000/health
wait_for http://127.0.0.1:9000/health
wait_for http://127.0.0.1:9100/health

COPILOT_PORT="${COPILOT_PORT:-8080}" "$PY" -m copilot 2> var/logs/copilot.log & pids+=($!)
wait_for "http://127.0.0.1:${COPILOT_PORT:-8080}/api/health/live"
echo "Stack is up:  GUI http://127.0.0.1:${COPILOT_PORT:-8080}   (logs in var/logs/)"
wait
