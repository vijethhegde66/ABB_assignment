"""Hybrid retriever: dense (embedding cosine) + sparse (BM25) fused with Reciprocal Rank Fusion,
then re-ranked with metadata boosts, filtered, screened for prompt injection and scored for confidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from rag.ingestion.chunker import Chunk
from rag.ingestion.embeddings import Embedder, get_embedder
from rag.ingestion.index_store import VectorIndex
from rag.ingestion.safety import sanitize
from rag.retrieval.bm25 import BM25
from rag.text import expand_query_tokens, tokenize

Confidence = Literal["high", "medium", "low", "none"]
RRF_K = 60


class RetrievalFilters(BaseModel):
    doc_types: list[str] | None = None
    equipment_classes: list[str] | None = Field(
        default=None, description="match any; 'general' docs always pass"
    )
    alarm_codes: list[str] | None = Field(default=None, description="boost only - never excludes")
    sites: list[str] | None = None
    trust_levels: list[str] | None = None
    doc_ids: list[str] | None = None
    exclude_quarantined: bool = True


class Citation(BaseModel):
    citation_id: str
    doc_id: str
    title: str
    doc_type: str
    section: str
    section_number: str | None
    revision: str | None
    effective_date: str | None
    trust_level: str
    source_path: str
    chunk_id: str
    snippet: str
    score: float

    def label(self) -> str:
        sec = f"§{self.section_number}" if self.section_number else self.section
        rev = f", rev {self.revision}" if self.revision else ""
        return f"{self.doc_id} {sec} – {self.section}{rev}"


class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    heading_path: list[str]
    score: float
    dense_score: float
    bm25_score: float
    matched_terms: list[str]
    metadata: dict[str, Any]
    sanitized: bool = False


class RetrievalResult(BaseModel):
    query: str
    filters: RetrievalFilters
    hits: list[RetrievedChunk]
    confidence: Confidence
    confidence_reason: str
    quarantined: list[dict[str, Any]] = Field(default_factory=list)
    candidates_considered: int = 0


@dataclass
class RetrieverConfig:
    top_k: int = 5
    candidate_pool: int = 30
    min_dense_for_low: float = 0.08
    high_dense: float = 0.30
    high_coverage: float = 0.5
    low_coverage: float = 0.2
    relative_floor: float = 0.75  # drop candidates scoring below 75% of the best fused score
    boosts: dict[str, float] = field(
        default_factory=lambda: {
            "chunk_alarm_code": 0.03,
            "alarm_code": 0.004,
            "equipment_class": 0.006,
            "controlled": 0.004,
            "heading_term": 0.006,
            "uncontrolled": -0.006,
        }
    )


class HybridRetriever:
    def __init__(self, index: VectorIndex, embedder: Embedder, config: RetrieverConfig | None = None) -> None:
        if index.embedder_name and index.embedder_name != embedder.name:
            raise ValueError(f"index built with {index.embedder_name}, retriever uses {embedder.name}")
        self.index = index
        self.embedder = embedder
        self.config = config or RetrieverConfig()
        self._tokens = [tokenize(c.embedding_text) for c in index.chunks]
        self._heading_tokens = [tokenize(c.heading) for c in index.chunks]
        self.bm25 = BM25(self._tokens)

    @classmethod
    def from_directory(cls, index_dir: Path, provider: str = "hashing") -> HybridRetriever:
        return cls(VectorIndex.load(index_dir), get_embedder(provider))

    # ------------------------------------------------------------------ filtering
    @staticmethod
    def _passes(chunk: Chunk, f: RetrievalFilters) -> bool:
        m = chunk.metadata
        if f.doc_ids and chunk.doc_id not in f.doc_ids:
            return False
        if f.doc_types and m.get("doc_type") not in f.doc_types:
            return False
        if f.trust_levels and m.get("trust_level") not in f.trust_levels:
            return False
        if f.sites:
            sites = m.get("sites") or []
            if sites and not set(sites) & set(f.sites):  # type: ignore[arg-type]
                return False
        if f.equipment_classes:
            classes = set(m.get("equipment_classes") or [])  # type: ignore[arg-type]
            if "general" not in classes and not classes & set(f.equipment_classes):
                return False
        return True

    # ------------------------------------------------------------------ search
    def search(
        self, query: str, filters: RetrievalFilters | None = None, top_k: int | None = None
    ) -> RetrievalResult:
        f = filters or RetrievalFilters()
        k = top_k or self.config.top_k
        q_tokens = expand_query_tokens(tokenize(query))
        if not q_tokens or not self.index.chunks:
            return RetrievalResult(
                query=query,
                filters=f,
                hits=[],
                confidence="none",
                confidence_reason="empty query or empty index",
            )
        eligible = [i for i, c in enumerate(self.index.chunks) if self._passes(c, f)]
        quarantined = []
        if f.exclude_quarantined:
            q_ids = [i for i in eligible if self.index.chunks[i].metadata.get("quarantined")]
            eligible = [i for i in eligible if i not in set(q_ids)]
        else:
            q_ids = []
        if not eligible:
            return RetrievalResult(
                query=query,
                filters=f,
                hits=[],
                confidence="none",
                confidence_reason="no documents match the retrieval filters",
            )

        qvec = self.embedder.embed([query + " " + " ".join(q_tokens)])[0]
        dense = self.index.embeddings[eligible] @ qvec
        sparse = np.array([self.bm25.score(q_tokens, i) for i in eligible])
        dense_rank = {eligible[i]: r for r, i in enumerate(np.argsort(-dense))}
        sparse_rank = {eligible[i]: r for r, i in enumerate(np.argsort(-sparse))}
        dense_by = {eligible[i]: float(dense[i]) for i in range(len(eligible))}
        sparse_by = {eligible[i]: float(sparse[i]) for i in range(len(eligible))}

        informative = list(dict.fromkeys(tokenize(query)))
        fused: dict[int, float] = {}
        for idx in eligible:
            s = 1 / (RRF_K + dense_rank[idx] + 1) + 1 / (RRF_K + sparse_rank[idx] + 1)
            m = self.index.chunks[idx].metadata
            b = self.config.boosts
            if f.alarm_codes and set(f.alarm_codes) & set(m.get("chunk_alarm_codes") or []):  # type: ignore[arg-type]
                s += b["chunk_alarm_code"]
            elif f.alarm_codes and set(f.alarm_codes) & set(m.get("alarm_codes") or []):  # type: ignore[arg-type,call-overload]
                s += b["alarm_code"]
            if f.equipment_classes and set(f.equipment_classes) & set(m.get("equipment_classes") or []):  # type: ignore[arg-type,call-overload]
                s += b["equipment_class"]
            s += b["controlled"] if m.get("trust_level") == "controlled" else b["uncontrolled"]
            # section headings are strong relevance signals in procedures ("4.2 BFP Suction Pressure Low")
            heading_hits = len(set(informative) & set(self._heading_tokens[idx]))
            if heading_hits >= 2:
                s += b["heading_term"] * heading_hits
            if sparse_by[idx] == 0 and dense_by[idx] < self.config.min_dense_for_low:
                continue  # no lexical evidence and negligible semantic similarity
            fused[idx] = s

        ranked = sorted(fused, key=lambda i: -fused[i])[: self.config.candidate_pool]
        if ranked:
            floor = fused[ranked[0]] * self.config.relative_floor
            ranked = [i for i in ranked if fused[i] >= floor]
        hits: list[RetrievedChunk] = []
        per_doc: dict[str, int] = {}
        for idx in ranked:
            chunk = self.index.chunks[idx]
            if per_doc.get(chunk.doc_id, 0) >= 2:  # diversity: max 2 chunks per document
                continue
            per_doc[chunk.doc_id] = per_doc.get(chunk.doc_id, 0) + 1
            text, changed = sanitize(chunk.text)
            matched = sorted({t for t in informative if t in set(self._tokens[idx])})
            hits.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    text=text,
                    heading_path=chunk.heading_path,
                    score=round(fused[idx], 5),
                    dense_score=round(dense_by[idx], 4),
                    bm25_score=round(sparse_by[idx], 3),
                    matched_terms=matched,
                    metadata={k: v for k, v in chunk.metadata.items() if k not in {"file_hash"}}
                    | {"section_number": chunk.section_number, "heading": chunk.heading},
                    sanitized=changed,
                )
            )
            if len(hits) >= k:
                break

        confidence, reason = self._confidence(hits, informative)
        if confidence == "none":
            hits = []
        for i in q_ids:
            c = self.index.chunks[i]
            if set(tokenize(c.embedding_text)) & set(q_tokens):
                quarantined.append(
                    {
                        "chunk_id": c.chunk_id,
                        "doc_id": c.doc_id,
                        "signals": c.metadata.get("injection_signals", []),
                        "reason": "suspected prompt injection - excluded from grounding",
                    }
                )
        return RetrievalResult(
            query=query,
            filters=f,
            hits=hits,
            confidence=confidence,
            confidence_reason=reason,
            quarantined=quarantined,
            candidates_considered=len(eligible),
        )

    def _confidence(self, hits: list[RetrievedChunk], informative: list[str]) -> tuple[Confidence, str]:
        if not hits:
            return "none", "no passage matched the query"
        top = hits[0]
        coverage = len(top.matched_terms) / max(1, len(informative))
        c = self.config
        if top.dense_score >= c.high_dense and coverage >= c.high_coverage:
            return "high", f"top passage covers {coverage:.0%} of query terms (cosine {top.dense_score:.2f})"
        if coverage >= c.low_coverage or top.dense_score >= c.high_dense * 0.75:
            return "medium", f"partial match: {coverage:.0%} term coverage (cosine {top.dense_score:.2f})"
        if coverage > 0 or top.dense_score >= c.min_dense_for_low * 2:
            return "low", f"weak match: {coverage:.0%} term coverage (cosine {top.dense_score:.2f})"
        return "none", "no meaningful overlap between query and documents"


def build_citations(hits: list[RetrievedChunk], start: int = 1, snippet_chars: int = 320) -> list[Citation]:
    out = []
    for n, h in enumerate(hits, start=start):
        m = h.metadata
        snippet = " ".join(h.text.split())
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars].rsplit(" ", 1)[0] + " …"
        out.append(
            Citation(
                citation_id=f"D{n}",
                doc_id=h.doc_id,
                title=str(m.get("title", h.doc_id)),
                doc_type=str(m.get("doc_type", "")),
                section=str(m.get("heading", h.heading_path[-1])),
                section_number=m.get("section_number"),
                revision=m.get("revision"),
                effective_date=m.get("effective_date"),
                trust_level=str(m.get("trust_level", "uncontrolled")),
                source_path=str(m.get("source_path", "")),
                chunk_id=h.chunk_id,
                snippet=snippet,
                score=h.score,
            )
        )
    return out
