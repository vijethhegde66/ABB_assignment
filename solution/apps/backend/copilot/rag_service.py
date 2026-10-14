"""Retrieval service used by the orchestrator (wraps the RAG package)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from copilot.models import RetrievalTrace
from platform_common import get_logger
from rag.ingestion.embeddings import get_embedder
from rag.ingestion.index_store import IndexNotFoundError, VectorIndex
from rag.ingestion.pipeline import ingest
from rag.retrieval.retriever import (
    Citation,
    HybridRetriever,
    RetrievalFilters,
    RetrievalResult,
    RetrievedChunk,
    build_citations,
)

log = get_logger("copilot.rag")


class RagUnavailableError(RuntimeError):
    pass


class RagService:
    def __init__(
        self,
        index_path: str,
        document_path: str,
        embedding_provider: str = "hashing",
        auto_ingest: bool = True,
    ) -> None:
        self.index_path = Path(index_path)
        self.document_path = Path(document_path)
        self.provider = embedding_provider
        self.auto_ingest = auto_ingest
        self.retriever: HybridRetriever | None = None
        self.error: str | None = None
        self.load()

    def load(self) -> None:
        embedder = get_embedder(self.provider)
        try:
            index = VectorIndex.load(self.index_path)
            if index.embedder_name != embedder.name:
                raise ValueError("embedder mismatch")
        except (IndexNotFoundError, ValueError) as exc:
            if not self.auto_ingest or not self.document_path.is_dir():
                self.error = str(exc)
                log.error("rag_index_unavailable", extra={"error": str(exc)})
                return
            log.info("rag_index_missing_auto_ingest", extra={"docs": str(self.document_path)})
            index, _ = ingest(self.document_path, self.index_path, embedder)
        self.retriever = HybridRetriever(index, embedder)
        self.error = None

    def reindex(self, rebuild: bool = False) -> dict[str, Any]:
        index, report = ingest(
            self.document_path, self.index_path, get_embedder(self.provider), rebuild=rebuild
        )
        self.retriever = HybridRetriever(index, get_embedder(self.provider))
        self.error = None
        return report.as_dict()

    def status(self) -> dict[str, Any]:
        if self.retriever is None:
            return {"status": "unavailable", "error": self.error}
        idx = self.retriever.index
        return {
            "status": "ok",
            "chunks": len(idx.chunks),
            "documents": len(idx.manifest.get("documents", {})),
            "embedder": idx.embedder_name,
            "built_at": idx.manifest.get("built_at"),
            "quarantined_chunks": sum(1 for c in idx.chunks if c.metadata.get("quarantined")),
        }

    def documents(self) -> list[dict[str, Any]]:
        if self.retriever is None:
            return []
        return [{"doc_id": k, **v} for k, v in self.retriever.index.manifest.get("documents", {}).items()]

    async def search(
        self, purpose: str, query: str, filters: RetrievalFilters | None = None, top_k: int = 5
    ) -> tuple[RetrievalResult, RetrievalTrace]:
        if self.retriever is None:
            raise RagUnavailableError(self.error or "retrieval index not loaded")
        t0 = time.perf_counter()
        result = await asyncio.to_thread(self.retriever.search, query, filters, top_k)
        trace = RetrievalTrace(
            purpose=purpose,
            query=query,
            filters=(filters or RetrievalFilters()).model_dump(exclude_none=True),
            confidence=result.confidence,
            confidence_reason=result.confidence_reason,
            hits=[
                {
                    "chunk_id": h.chunk_id,
                    "doc_id": h.doc_id,
                    "score": h.score,
                    "dense": h.dense_score,
                    "bm25": h.bm25_score,
                    "section": h.metadata.get("heading"),
                    "sanitized": h.sanitized,
                }
                for h in result.hits
            ],
            quarantined=result.quarantined,
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        log.info(
            "rag_search",
            extra={
                "purpose": purpose,
                "retrieval_query": query,
                "confidence": result.confidence,
                "doc_ids": [h.doc_id for h in result.hits],
                "top_score": result.hits[0].score if result.hits else None,
            },
        )
        return result, trace


class CitationRegistry:
    """Assigns stable [D#] ids to retrieved chunks across all retrievals of one turn."""

    def __init__(self) -> None:
        self._by_chunk: dict[str, Citation] = {}
        self.chunks: dict[str, RetrievedChunk] = {}

    def add(self, hits: list[RetrievedChunk]) -> list[str]:
        ids = []
        for h in hits:
            if h.chunk_id not in self._by_chunk:
                cit = build_citations([h], start=len(self._by_chunk) + 1)[0]
                self._by_chunk[h.chunk_id] = cit
                self.chunks[cit.citation_id] = h
            ids.append(self._by_chunk[h.chunk_id].citation_id)
        return ids

    def get(self, citation_id: str) -> Citation | None:
        return next((c for c in self._by_chunk.values() if c.citation_id == citation_id), None)

    def all(self) -> list[Citation]:
        return sorted(self._by_chunk.values(), key=lambda c: int(c.citation_id[1:]))
