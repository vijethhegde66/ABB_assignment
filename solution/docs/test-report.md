# Test Report & Coverage

Command: `make coverage` (= `pytest --cov`). Result on the reference machine (macOS, Python 3.12.15):
**152 passed, 0 failed, 93 % line+branch coverage** (gate: 80 %, enforced in `pyproject.toml`).
HTML report: `var/coverage-html/index.html` (generated, git-ignored); CI uploads `coverage.xml` as an artifact.

## Test inventory

| Suite | File | Tests | What it proves |
|---|---|---:|---|
| Unit | `tests/unit/test_intent.py` | 17 | intent classification for all sample questions, entity extraction, context references |
| Unit | `tests/unit/test_evidence.py` | 10 | focal-alarm selection, cause ranking, consistency (conflict / supported / not covered), procedure steps |
| Unit | `tests/unit/test_composer_and_llm.py` | 5 | grounded Markdown, citation formatting, LLM validation (unknown refs, unsafe wording), Anthropic provider mapping + refusal |
| Unit | `tests/unit/test_platform_and_memory.py` | 5 | secret redaction, JSON logs with trace ids, tool-error parsing, conversation LRU/TTL |
| API client | `tests/unit/test_alarm_connector.py` | 16 | headers/payloads, retries + backoff, Retry-After, deadline, error mapping matrix, timeouts, bad payloads, pagination |
| Simulator | `tests/unit/test_simulator_store.py` | 10 | determinism, filters, KPIs, correlation recovers embedded chain, trend, priority, floods, calculations |
| Contract | `tests/integration/test_simulator_contract.py` | 6 | replay of all 3 Postman collections with chaining, auth, validation, 404, fault injection |
| MCP server | `tests/integration/test_mcp_server.py` | 19 | registration, discovery, schemas, validation, auth failure, pagination, retry, timeout, trace propagation, partial ranking, every tool, HTTP bearer boundary |
| MCP client | `tests/integration/test_mcp_client.py` | 8 | connectivity, discovery, invocation, client-side invalid args, missing tool, approval policy, one server down, client timeout |
| MCP (2nd server) | `tests/integration/test_work_orders_mcp.py` | 2 | draft → submit, single use, forged/foreign tokens rejected |
| Orchestration | `tests/integration/test_orchestration.py` | 11 | multi-step chain, output→input passing, RAG in same workflow, grounded refs, conflicts, context retention, partial failure, MCP down, clarification, injection resistance, LLM path |
| Backend API | `tests/integration/test_copilot_api.py` | 4 | chat contract, validation, tools/health/documents/rag search, work-request confirmation |
| RAG | `rag/tests/test_ingestion.py` | 11 | md/html/txt/pdf extraction + metadata, chunking/offsets/overlap, quarantine, incremental refresh |
| RAG | `rag/tests/test_retrieval.py` | 24 | top-1 relevance table, recall@3, filters, diversity, no-result, citation correctness, injection detection/quarantine/redaction |
| E2E | `tests/e2e/test_acceptance_scenario.py` | 3 | **mandatory acceptance scenario over real HTTP** (all services on ports, bearer auth, Streamable HTTP), degraded scenario, MCP auth |
| E2E GUI | `tests/e2e/test_gui.py` | 1 | headless Chromium drives the GUI: answer, citation chips, sources, trace with raw API calls, tool catalog, no JS errors |

## Coverage by module

```
apps/backend/copilot/__init__.py                          1      0      0      0   100%
apps/backend/copilot/api.py                             154      8     14      3    93%   67->76, 193-194, 213, 253-255, 268-269, 273->280
apps/backend/copilot/composer.py                        148      5     82      3    95%   30, 82->92, 200-203
apps/backend/copilot/config.py                           35      0      0      0   100%
apps/backend/copilot/evidence.py                        133      2     54      4    97%   47->45, 98, 158->161, 233
apps/backend/copilot/intent.py                          115      2     48      4    96%   199, 208->206, 221->219, 249
apps/backend/copilot/llm.py                              55     12      8      2    78%   69-74, 80, 104-108
apps/backend/copilot/mcp_client.py                      197     19     36      4    89%   120->103, 123-126, 202-214, 228-231, 254->258, 256-257, 304->291
apps/backend/copilot/memory.py                           42      0      4      0   100%
apps/backend/copilot/models.py                           97      0      0      0   100%
apps/backend/copilot/orchestrator.py                    509     52    220     39    86%   130-135, 169->201, 176->188, 194->200, 196, 228->230, 245, 253, 269, 278-279, 292-299, 312-313, 318-319, 321->328, 354, 423->441, 441->443, 464, 475, 497-505, 507, 520-521, 527, 557->exit, 564-575, 579-580, 585-586, 589, 590->exit, 637->641, 653->652, 666->676, 676->687, 703->exit, 712-716, 723->718, 842-847, 853, 936->926, 965-966, 982-983, 1136->1138
apps/backend/copilot/rag_service.py                      77     13     14      4    79%   51-52, 55-57, 64-69, 73, 86, 93, 148
apps/simulator/alarm_simulator/__init__.py                1      0      0      0   100%
apps/simulator/alarm_simulator/app.py                   181      5     30      3    96%   104, 106, 152, 222, 335
apps/simulator/alarm_simulator/catalog.py                52      1      4      1    96%   939
apps/simulator/alarm_simulator/config.py                 31      4      4      2    83%   10-11, 36, 42
apps/simulator/alarm_simulator/generator.py             170      1     50      1    99%   104
apps/simulator/alarm_simulator/models.py                 81      2      6      2    95%   40, 44
apps/simulator/alarm_simulator/recommendations.py        30      0      6      0   100%
apps/simulator/alarm_simulator/store.py                 394     15    166     17    94%   86, 90, 94, 188, 204, 206, 259, 261, 269-270, 303->301, 314-315, 349->352, 383->367, 448, 503->507, 657, 669
connectors/alarm_connector/__init__.py                    4      0      0      0   100%
connectors/alarm_connector/client.py                    170      4     20      1    97%   69, 72, 91-92, 122->209
connectors/alarm_connector/config.py                     15      0      0      0   100%
connectors/alarm_connector/errors.py                     43      0     10      0   100%
libs/platform_common/__init__.py                          3      0      0      0   100%
libs/platform_common/asgi_auth.py                        21      0      4      0   100%
libs/platform_common/logging.py                          55      2     22      2    95%   42, 78
libs/platform_common/tracing.py                          42      0      4      0   100%
mcp-servers/alarm-management/alarm_mcp/__init__.py        2      0      0      0   100%
mcp-servers/alarm-management/alarm_mcp/app.py             8      0      0      0   100%
mcp-servers/alarm-management/alarm_mcp/config.py         12      0      0      0   100%
mcp-servers/alarm-management/alarm_mcp/schemas.py       188      0      0      0   100%
mcp-servers/alarm-management/alarm_mcp/server.py        258     16     32     12    90%   83, 86, 90, 106->115, 109-110, 113-114, 137->139, 156, 362, 406, 496, 542, 602->604, 610-611, 734-735
mcp-servers/work-orders/work_orders_mcp/__init__.py       2      0      0      0   100%
mcp-servers/work-orders/work_orders_mcp/server.py        97      4     10      2    94%   155, 231-232, 241
rag/ingestion/__init__.py                                 0      0      0      0   100%
rag/ingestion/chunker.py                                 85      1     22      2    97%   77->87, 113
rag/ingestion/cli.py                                     24     24      4      0     0%   6-42
rag/ingestion/embeddings.py                              45      1     16      1    97%   65->59, 88
rag/ingestion/extractors.py                             126      3     58      6    95%   35, 38, 128, 130->132, 149->156, 151->156
rag/ingestion/index_store.py                             52      2     14      3    92%   32, 61, 65->64
rag/ingestion/pipeline.py                                93      8     22      3    90%   60->69, 64-65, 84-87, 90-91
rag/ingestion/safety.py                                  36      0     10      0   100%
rag/retrieval/__init__.py                                 0      0      0      0   100%
rag/retrieval/bm25.py                                    25      1      6      0    97%   37
rag/retrieval/retriever.py                              186      3     60      2    98%   114, 125, 262
TOTAL                                                  4095    210   1060    123    93%
152 passed in 26.59s
```
