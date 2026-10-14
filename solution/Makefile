PY ?= .venv/bin/python
export PYTHONPATH := libs:apps/simulator:connectors:mcp-servers/alarm-management:mcp-servers/work-orders:apps/backend:.

.PHONY: install lint format typecheck test test-unit test-mcp test-rag test-e2e coverage ingest run mcp up down screenshots audit

install:            ## create venv and install dev dependencies
	python3.12 -m venv .venv && $(PY) -m pip install -r requirements-dev.txt && $(PY) -m playwright install chromium

lint:               ## ruff lint + format check
	$(PY) -m ruff check . && $(PY) -m ruff format --check .

format:
	$(PY) -m ruff format . && $(PY) -m ruff check --fix .

typecheck:
	$(PY) -m mypy libs apps/simulator/alarm_simulator connectors/alarm_connector mcp-servers/alarm-management/alarm_mcp mcp-servers/work-orders/work_orders_mcp rag/ingestion rag/retrieval apps/backend/copilot

test:               ## full suite
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest tests/unit
test-mcp:
	$(PY) -m pytest tests/integration/test_mcp_server.py tests/integration/test_mcp_client.py tests/integration/test_work_orders_mcp.py
test-rag:
	$(PY) -m pytest rag/tests
test-e2e:
	$(PY) -m pytest tests/e2e -m e2e

coverage:
	$(PY) -m pytest --cov --cov-report=term --cov-report=html:var/coverage-html --cov-report=xml:var/coverage.xml

ingest:             ## build / refresh the retrieval index
	$(PY) -m rag.ingestion.cli --docs rag/documents --index var/rag-index

mcp:                ## run the Alarm MCP server alone (Streamable HTTP on :9000)
	$(PY) -m alarm_mcp

run:                ## run the whole stack locally without Docker
	scripts/run_local.sh

up:
	docker compose up --build
down:
	docker compose down -v

screenshots:
	$(PY) scripts/capture_screenshots.py --url http://127.0.0.1:8080

audit:
	$(PY) -m pip_audit -r requirements.txt
