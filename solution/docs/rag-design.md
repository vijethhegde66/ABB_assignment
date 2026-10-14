# RAG Design

## 1. Source document types (`rag/documents/`)

| doc_id | Type | Format | Trust | Purpose |
|---|---|---|---|---|
| SOP-BFP-001 | operating_procedure | Markdown | controlled | BFP operation, alarm response §4.1–4.6, recurring alarms §5, changeover §6 |
| MM-BFP-002 | maintenance_manual | Markdown | controlled | Lubrication, bearings (monitoring-only not acceptable >30 min above limit), strainer, seal, vibration |
| TG-BFP-003 | troubleshooting_guide | Markdown | controlled | Causes of recurring suction/discharge pressure, bearing temperature, investigation workflow |
| SOP-CMP-010 | operating_procedure | Markdown | controlled | Compressor discharge pressure / surge / temperature response |
| TG-CMP-011 | troubleshooting_guide | Markdown | controlled | Why discharge pressure alarms repeat (intercooler, CW, anti-surge valve, deadband) |
| SAF-ELE-020 | safety_instruction | Markdown | controlled | Motor trip response (no reset/restart before cause found), related equipment to inspect, LOTO |
| MM-MTR-021 | maintenance_manual | Markdown | controlled | Induction motor winding temperature, current, starts, insulation tests |
| ALM-PHIL-030 | alarm_philosophy | **HTML** | controlled | Priority definitions/response times, KPI targets, recurring alarms, rationalisation |
| SAF-GEN-040 | safety_instruction | Markdown | controlled | Protective systems, permits, fired heaters, reactors, PPE |
| RN-2026-014 | resolution_notes | **Text** | controlled | Previous BFP 101 incident root cause (LCV-100 positioner) |
| KB-VENDOR-099 | vendor_bulletin | Markdown | **uncontrolled** | Contains an embedded prompt-injection payload (test fixture) |

PDF is also supported (`pypdf`; metadata from the PDF info dict or a `<name>.meta.yaml` sidecar) and covered by
tests with a generated PDF. All documents are synthetic.

## 2. Ingestion flow

```
discover(files) → extract(text, metadata) → per-chunk injection assessment → heading-aware chunking
→ chunk metadata (+ chunk-level alarm codes) → embeddings for new/changed docs only → atomic index write
```

Command: `python -m rag.ingestion.cli --docs rag/documents --index var/rag-index [--rebuild]`
(Docker: the one-shot `rag-ingest` service; locally `make ingest`; the backend auto-ingests if the index is missing
and `RAG_AUTO_INGEST=true`). The CLI prints an ingestion report (added/updated/unchanged/removed/failed, chunk
count, flagged chunks).

## 3. Text extraction

* Markdown / text: YAML front matter → metadata; body kept as Markdown (headings preserved).
* HTML: stdlib `HTMLParser`; `<script>/<style>/<template>` dropped; `<meta name=… content=…>` → metadata;
  headings → `#`, list items → `- `, table rows → `| a | b |`.
* PDF: page text with page markers.
* Unsupported types, empty documents and duplicate `doc_id`s are reported as failures without stopping ingestion.

## 4. Chunking strategy

Heading-aware: split on Markdown headings first so a chunk never straddles two sections (procedure steps stay
with their heading). Sections longer than 1,100 chars are split on sentence/list boundaries with 150-char overlap.
Heading-only sections are skipped (their children carry the content). Each chunk embeds with a **contextual header**
(`<document title> > <heading path>`) which markedly improves retrieval of short procedure steps.

## 5. Chunk metadata

`chunk_id` (`DOC#ordinal-slug`), `doc_id`, `title`, `doc_type`, `equipment_classes`, `alarm_codes` (document level),
`chunk_alarm_codes` (codes that actually appear in the section), `sites`, `revision`, `effective_date`, `owner`,
`trust_level`, `heading`, `heading_path`, `section_number`, `char_start/char_end` (offsets into the extracted text),
`content_hash`, `source_path`, `source_format`, `file_hash`, `injection_score`, `injection_signals`, `quarantined`.

## 6. Embeddings and index

* Default embedder `hashing-v1-1024`: signed feature hashing of stemmed unigrams (w=2), bigrams and character
  4-grams, sublinear TF, L2-normalised. Deterministic, offline, fast, no model download (CI and air-gapped plants).
* Optional `sentence-transformers` (`EMBEDDING_PROVIDER=sentence-transformers`) for semantic embeddings.
* The index records its embedder; a mismatch triggers a rebuild (ingestion) or an error (retriever).
* Store: `chunks.jsonl` + `embeddings.npy` + `manifest.json` written atomically. `VectorIndex` is the only
  interface the retriever needs → pgvector/Qdrant/OpenSearch can replace it.

## 7. Hybrid search, ranking and filters

1. Query tokens are stemmed and expanded with a small domain synonym table (e.g. `bfp → boiler feed pump`,
   `cavitation → suction pressure npsh`).
2. **Filters** (hard): `doc_types`, `equipment_classes` (documents tagged `general` always pass), `sites`,
   `trust_levels`, `doc_ids`, `exclude_quarantined` (default true).
3. **Dense** cosine over embeddings + **BM25** (k1=1.4, b=0.75) over the contextualised chunk text.
4. **Reciprocal Rank Fusion** (k=60) + metadata **boosts**: chunk-level alarm-code match (+0.03), document-level
   alarm code (+0.004), equipment class (+0.006), section-heading term matches (+0.006 each, ≥2 terms),
   controlled (+0.004) / uncontrolled (−0.006).
5. Candidates with no lexical evidence and negligible cosine are dropped; candidates below 75 % of the best fused
   score are dropped; at most 2 chunks per document (diversity).

The orchestrator builds filters from MCP data: e.g. the applicable-procedure query for the focal alarm uses
`doc_types=[operating_procedure, safety_instruction]`, the alarm's equipment class and its related assets' classes,
the asset's **site** (so a NorthPlant-only SOP is never cited for an EastRefinery motor) and `alarm_codes=[code]`.

## 8. Confidence and low-confidence handling

`confidence` is computed from the top passage: query-term coverage and cosine.
`high` (cos ≥ 0.30 and coverage ≥ 50 %), `medium`, `low`, `none`. The orchestrator only grounds on high/medium
passages; for `low/none` it states that no controlled document matched (e.g. *"No controlled operating procedure
matched '<alarm>' with sufficient confidence – follow the general alarm response and consult the shift
supervisor"*) instead of guessing. Off-topic queries (e.g. "chocolate cake recipe") return `none` with no hits.

## 9. Citation construction

Each grounded passage receives a turn-stable id `D1..Dn` (`CitationRegistry`, de-duplicated by chunk). A citation
carries doc id, title, doc type, section heading and number, revision, effective date, trust level, source path,
chunk id, snippet and score; `label()` renders e.g. `SOP-BFP-001 §4.2 – 4.2 BFP Suction Pressure Low (BFP_SP_LO), rev 7`.
The answer references them inline (`[D1]`), the GUI renders them as chips that jump to the Sources tab.
Tests assert the snippet text comes from the cited chunk and the section/revision are correct.

Example retrieved chunk (from `/api/rag/search`):

```json
{"citation_id": "D1", "doc_id": "SOP-BFP-001", "section": "4.2 BFP Suction Pressure Low (BFP_SP_LO)",
 "section_number": "4.2", "revision": "7", "trust_level": "controlled",
 "chunk_id": "SOP-BFP-001#004-4-2-bfp-suction-pressure-low-bfp-sp-lo",
 "snippet": "1. Check deaerator level (normal 45 - 75 %) and deaerator pressure. Restore make-up / condensate flow and verify the level control valve LCV-100 is responding. 2. Check the suction strainer differential pressure. …"}
```

## 10. Prompt-injection protections (defence in depth)

1. **Ingestion screening** – `rag.ingestion.safety.assess` scores instruction-like patterns (override previous
   instructions, text addressed to AI assistants, role hijack, prompt exfiltration, tool invocation, special
   tokens; a lone "never bypass a trip" is *not* flagged). Chunks ≥ 0.6 are marked `quarantined`.
2. **Retrieval quarantine** – quarantined chunks are excluded by default and reported in `RetrievalResult.quarantined`
   → shown as an evidence note and in the Retrieval tab.
3. **Sanitisation** – any returned passage is sentence-level sanitised; injected sentences become
   `[redacted: suspected prompt-injection content]`.
4. **Trust levels** – uncontrolled documents get a ranking penalty and are never used to mark a recommendation as
   supported or conflicting.
5. **No control flow from documents** – the tool plan is built by code from the user's intent and MCP outputs;
   document text can never trigger a tool call. Write tools additionally require human approval.
6. **LLM framing** – when an LLM is enabled, passages are wrapped in `<document>` tags and the system prompt
   declares them untrusted data; output is rejected if it cites unknown ids or contains unsafe recommendations.

Covered by `rag/tests/test_retrieval.py` and `tests/integration/test_orchestration.py::test_prompt_injection_document_cannot_steer_answer`.

## 11. Index refresh

Re-run the ingestion command. It compares file SHA-256 hashes with the manifest: unchanged documents reuse
stored chunks and vectors, changed documents are re-chunked and re-embedded, deleted documents are removed;
`--rebuild` forces a full rebuild (also automatic when the embedder changes). The backend exposes
`RagService.reindex()` for an admin hook. Writes are atomic (temp dir + rename).
