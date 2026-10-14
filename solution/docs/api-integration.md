# Alarm Management API Integration

## 1. Contract source

The Postman collections supplied with the assignment are the specification. They are copied to
`test-data/postman/` and **replayed automatically** by `tests/integration/test_simulator_contract.py`
(every request of all three collections, in order, with collection-variable chaining that mirrors the Postman
test scripts: `asset_id`, `asset_id_2/3`, `alarm_id`, `calculation_id`, `window_start/end`). The replay asserts
HTTP 200, non-empty results where the Postman scripts assert `above(0)`, and `trace_id` echo.

They can also be run with Newman against a running simulator:
`newman run test-data/postman/Alarm-API-Chaining.postman_collection.json --env-var baseUrl=http://localhost:8000 --env-var auth_token=$ALARM_API_TOKEN`.

## 2. Simulator (`apps/simulator`)

| Endpoint | Notes |
|---|---|
| `GET /health` | public; dataset size and window |
| `GET /assets/search?query&site&unit&asset_type&limit` | token/number-aware fuzzy match, `match_score` |
| `GET /assets/{asset_id}/metadata` | criticality, limits, `related_assets` (relationship graph), `procedure_refs` |
| `GET /alarms?asset_id&site&unit&status&severity&alarm_type&alarm_name&start_time&end_time&page&page_size&sort_by&sort_order` | pagination `{page, page_size, total_items, total_pages, has_next}`; `sort_by` allow-list |
| `GET /alarms/{alarm_id}` | |
| `POST /alarms/summary` | `group_by` × `kpis` (alarm_count, recurring_rate, avg_ack_delay, critical_count, chattering_count, standing_count, suppression_candidate_rate, unacknowledged_count) |
| `POST /alarms/trends` | hourly/daily/weekly buckets + trend direction |
| `POST /alarms/correlation` | co-occurrence within a lag window over the assets **and their related assets**: support, confidence, lift, average lag, leading indicators |
| `POST /alarms/flood-analysis` | rolling-window flood detection |
| `POST /alarms/rationalization-candidates` | frequent / chattering / stale / often-unacknowledged |
| `POST /alarms/priority-score` | 0–100 score with factors (severity, criticality, recurrence, unacknowledged, safety/electrical) |
| `POST /recommendations/operator-actions` | rule-engine actions (+ related assets, asset context, history). Some rules intentionally conflict with site procedures. |
| `POST /calculation-code/generate` / `execute` | declarative KPI definitions; the generated "code" is descriptive and **never evaluated** |
| `GET /analytics/kpi-definitions` | |
| `GET/POST/DELETE /_sim/faults`, `GET /_sim/requests` | test harness: fault injection (error/timeout/latency, count-limited) and request log with trace ids |

Data: 26 assets in 3 sites / 6 units and about 3,200 alarms from April 2026 to now, generated deterministically
per day (`SIM_SEED`). The data has embedded causal chains: deaerator level → BFP suction → discharge → vibration;
lube filter → oil pressure → bearing temperature; intercooler → compressor temperature → discharge pressure;
anti-surge valve → surge; motor current → winding temperature → trip. It also has unit floods, chattering sources,
a worsening trend and a fixed set of alarms that are active at the end of the window.

Request validation: Pydantic models with `extra="forbid"`, ID patterns, enums, ranges, and timezone-aware ordered
time ranges of at most 400 days. Errors return `{"error": {"code", "message", "details", "trace_id"}}` (401/404/400/422).
Filtering runs over typed objects (no query strings are built), so there is no injection surface.

## 3. Connector (`connectors/alarm_connector`)

| Concern | Implementation |
|---|---|
| Auth | `Authorization: Bearer <ALARM_API_TOKEN>` (SecretStr; redacted from `CallRecord.body` and logs) |
| Trace headers | `trace_id`, `x-request-id` (new per call), `x-client-id: alarm-mcp-server`, `x-metadata-tag: <operation>` |
| Timeouts | connect 3 s, per attempt 10 s, total deadline 30 s across retries |
| Retries | 429/502/503/504, timeouts, connection errors; exponential backoff + jitter; `Retry-After` honoured (capped); other 4xx never retried |
| Error mapping | 400/409/422→`InvalidArgumentError`, 401/403→`AuthenticationError`, 404→`NotFoundError`, 429→`RateLimitedError`, 502/503/504/connection→`UpstreamUnavailableError`, timeout→`UpstreamTimeoutError`, non-JSON or non-object→`BadUpstreamResponseError` |
| Pagination | `list_alarms_all_pages(max_pages)` with `pages_fetched` and `truncated` |
| Observability | One `CallRecord` per logical call: method, path, params, redacted body, status, attempts, retry reasons, duration, outcome, error code, trace/request ids |

## 4. Postman request → MCP tool mapping

| Postman request(s) | MCP tool |
|---|---|
| 01 Search Asset | `search_assets` |
| 02 Asset Metadata | `get_asset_metadata` |
| 03 Get Alarms, CHAIN-05/09 active alarms | `get_alarms` |
| 04 Get Alarm by ID | `get_alarm_details` |
| 05 Alarm Summary | `summarize_alarms` |
| 06 Alarm Trends | `get_alarm_trends` |
| 07 Correlation, CHAIN-03/08 | `correlate_alarms` |
| 08 Flood Analysis, CHAIN-02 | `analyze_alarm_floods` |
| 09 Rationalization, CHAIN-06/10 | `find_rationalization_candidates` |
| 10 Priority Score, CHAIN-09 | `score_alarm_priority`, `rank_alarm_priorities` |
| 11 Operator Recommendations | `recommend_operator_actions` |
| 12+13 Generate/Execute calculation, CHAIN-04/07/10 | `calculate_kpi` (chains both) |
| 14 KPI Definitions | `list_kpi_definitions` |

The orchestrator stages reproduce the ten Postman chaining flows at copilot level. For example, CHAIN-03 (root
cause + recommendations) becomes `search_assets → correlate_alarms → get_alarms → get_asset_metadata →
recommend_operator_actions`. CHAIN-09 (EastRefinery active → priority → recommendation) is the `priority_ranking`
intent.
