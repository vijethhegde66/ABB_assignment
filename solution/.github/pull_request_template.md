## Summary
<!-- What does this PR change and why? -->

## Scope
- [ ] MCP server / tools
- [ ] MCP client / orchestration
- [ ] RAG ingestion / retrieval
- [ ] GUI
- [ ] Packaging / CI / docs

## Architecture changes
<!-- New components, boundaries, data flows. Update docs/architecture.md + diagram if needed. -->

## MCP tools added / changed
<!-- Name, schema change, error codes. Regenerate docs/mcp-tool-catalog.md (scripts/generate_tool_catalog.py). -->

## RAG workflow changes
<!-- Documents, chunking, filters, ranking, confidence. -->

## Screenshots
<!-- scripts/capture_screenshots.py -->

## Test evidence
<!-- `make lint typecheck coverage` output; e2e results -->

## Design decisions
## Known limitations

## Review checklist
- [ ] Copilot reaches the Alarm API only through MCP
- [ ] New tools have typed input/output schemas, validation, error mapping and tests
- [ ] Answers keep [T#]/[D#] citations; low-confidence paths tested
- [ ] No secrets in code, logs, fixtures or screenshots
- [ ] Write operations require explicit confirmation
- [ ] Docs updated (README, catalog, rag-design, limitations)
