# Demo Video Script (≤ 10 minutes)

Setup before recording: `docker compose up --build` (or `scripts/run_local.sh`), open http://localhost:3000
(Docker) or http://127.0.0.1:8080 (local). `ENABLE_DEMO_CONTROLS=true`. Clear faults.

| Time | Show | Say |
|---|---|---|
| 0:00–0:45 | Architecture diagram (`docs/architecture-diagram.png`) | Use case; MCP path vs RAG path; the copilot only reaches the Alarm API through the candidate MCP server; second MCP server for work orders. |
| 0:45–1:30 | Terminal: `docker compose ps` (all healthy); `curl localhost:9000/health`; `curl -X POST localhost:9000/mcp` → 401 | Independent MCP server with bearer auth; health checks and dependency ordering. |
| 1:30–2:15 | GUI header pills; **Tools** tab: expand `search_assets` and `submit_work_request` | Tool discovery: 14 + 3 tools, typed input/output schemas, read-only vs write (approval required). |
| 2:15–4:30 | Click the acceptance example (BFP 101, 90 days). Walk through the answer, then the **Investigation** tab | Asset resolution, KPIs, increasing trend, focal alarm, causes from correlation + work orders + documents with confidence, procedure steps, API recommendations checked against documents. Click a `[D1]` chip → Sources; click `[T6]` → trace. |
| 4:30–5:30 | **MCP trace** tab: expand `search_assets`, `correlate_alarms` | Chaining (asset_id → metadata → correlation), raw arguments, underlying API calls with trace_id, attempts, duration; same trace_id in the simulator log (`curl /_sim/requests?trace_id=…`). |
| 5:30–6:15 | **Retrieval** tab | Queries built from MCP output, filters (doc type, equipment class, site, alarm code), scores, confidence, quarantined vendor bulletin (prompt injection). |
| 6:15–7:15 | Ask "Which alarm has the highest priority in EastRefinery, and why?" then the follow-up "Are the API recommendations consistent with the maintenance manual?" | Priority ranking; **conflict detected**: the rule engine says "reset and restart", SAF-ELE-020 forbids it → follow the controlled procedure. Context retention. |
| 7:15–8:30 | **Demo controls**: "Transient 2×503" → rerun BFP 101 → trace shows 2 retries, status complete. Then "Outage: correlation endpoint down" → rerun | Retry with backoff vs persistent failure: status `partial`, red tool call with `UPSTREAM_UNAVAILABLE`, evidence-gaps section, answer still grounded. Clear faults. |
| 8:30–9:15 | Ask "Investigate recurring alarms on Boiler Feed Pump 102 over the last 90 days" → proposed work request card → Review & create → confirmation dialog → created | Write operation needs explicit confirmation; HMAC approval token enforced server-side. |
| 9:15–10:00 | Terminal: `make lint typecheck coverage` summary, `pytest tests/e2e -q`; mobile screenshot | 152 tests, 93 % coverage, contract replay of the Postman collections, real-HTTP e2e + GUI test; limitations and next steps. |
