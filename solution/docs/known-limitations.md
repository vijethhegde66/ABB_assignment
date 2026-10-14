# Known Limitations and Future Improvements

## Known limitations

1. **Synthetic data.** The Alarm API is a simulator built from the Postman contract; documents are synthetic.
   Correlation patterns are embedded by the generator, so causal findings are "true by construction".
2. **Rule-based intent detection.** The weighted cue classifier covers the eight supported intents and plant
   vocabulary; unusual phrasings may fall back to `investigate`/`document_question` or ask for clarification.
   There is no LLM planner.
3. **Default embeddings are lexical-semantic, not neural.** Paraphrases with no shared terms or sub-words (e.g.
   "the feed pump is starving") retrieve less well than with a neural model. `sentence-transformers` is pluggable
   but not part of the default image.
4. **Consistency checking is heuristic.** It detects the documented conflict classes (restart/reset, continued
   operation above limits, bypass/disable) plus lexical support. Subtle numeric conflicts (e.g. a different
   setpoint value) are not detected.
5. **Single-replica state.** Conversation memory and the work-order store are in-memory; the RAG index is a local
   file on a volume.
6. **Static bearer tokens** for the MCP endpoints and the API; there is no OAuth / per-user authorisation, no RBAC
   per tool, and the GUI has no login (the "approved by" name is self-declared).
7. **Per-request MCP sessions** add a small handshake cost per turn; there is no connection pooling and no
   caching of discovery across turns.
8. **The time window is relative to "now"** (or `COPILOT_REFERENCE_TIME`); there is no free-form date parsing
   beyond "last N days/weeks/months" and simple words.
9. **The demo fault-injection endpoints** are a test harness; they must stay disabled in production
   (`ENABLE_DEMO_CONTROLS=false`).
10. **The LLM path** has been tested with a fake provider and a stubbed SDK client, not against the live API in CI
    (no key in CI).
11. **Docker images** are defined but could not be built in the authoring environment (no Docker daemon). The
    stack was verified with `scripts/run_local.sh` and the real-HTTP end-to-end tests; the CI `build` job builds
    and smoke-tests the compose stack.

## Future improvements

* OAuth 2.1 / OIDC for the MCP servers (the MCP SDK's `TokenVerifier`) with per-user, per-tool authorisation and
  audit logging of write operations.
* Optional LLM planner (tool-use loop over the same registry and policy) for open-ended questions, with the
  deterministic planner as a guard-rail and fallback.
* Neural embeddings + cross-encoder re-ranking; evaluation set with recall@k / MRR tracked in CI.
* pgvector/Qdrant and Redis for multi-replica deployments; MCP session pooling and discovery caching (TTL).
* OpenTelemetry traces (the MCP SDK already emits spans) exported to Jaeger/Tempo; Prometheus metrics for tool
  latency, retry counts and retrieval confidence.
* Streaming responses (SSE) to show stage progress live in the GUI.
* Richer consistency checking (numeric limits, units) and a feedback loop that files rule-engine discrepancies.
* More document formats (DOCX, scanned PDFs with OCR) and document versioning (cite superseded revisions).
* Resources/prompts on the MCP server (e.g. alarm definitions as MCP resources).
