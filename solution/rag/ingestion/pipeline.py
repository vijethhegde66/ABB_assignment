"""Ingestion pipeline: discover -> extract -> screen -> chunk -> embed -> index (incremental)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from rag.ingestion.chunker import Chunk, chunk_document
from rag.ingestion.embeddings import Embedder
from rag.ingestion.extractors import SUPPORTED_SUFFIXES, ExtractionError, extract
from rag.ingestion.index_store import IndexNotFoundError, VectorIndex
from rag.ingestion.safety import assess

log = logging.getLogger("rag.ingestion")


@dataclass
class IngestReport:
    documents_total: int = 0
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    chunks_total: int = 0
    flagged_chunks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover(docs_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith(".")
    )


def ingest(
    docs_dir: Path,
    index_dir: Path,
    embedder: Embedder,
    *,
    rebuild: bool = False,
    max_chars: int = 1100,
    overlap: int = 150,
) -> tuple[VectorIndex, IngestReport]:
    report = IngestReport()
    previous: VectorIndex | None = None
    if not rebuild:
        try:
            previous = VectorIndex.load(index_dir)
            if previous.embedder_name != embedder.name:
                log.info("embedder changed (%s -> %s): full rebuild", previous.embedder_name, embedder.name)
                previous = None
        except (IndexNotFoundError, ValueError):
            previous = None

    prev_docs: dict[str, dict[str, Any]] = previous.manifest.get("documents", {}) if previous else {}
    prev_chunks: dict[str, list[tuple[Chunk, np.ndarray]]] = {}
    if previous:
        for c, vec in zip(previous.chunks, previous.embeddings, strict=True):
            prev_chunks.setdefault(c.doc_id, []).append((c, vec))

    kept: list[tuple[Chunk, np.ndarray | None]] = []
    documents: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()

    for path in discover(docs_dir):
        report.documents_total += 1
        file_hash = _file_hash(path)
        try:
            doc = extract(path)
        except (ExtractionError, UnicodeDecodeError, ValueError) as exc:
            report.failed[path.name] = str(exc)
            log.warning("extraction failed for %s: %s", path.name, exc)
            continue
        doc_id = str(doc.metadata["doc_id"])
        if doc_id in seen_ids:
            report.failed[path.name] = f"duplicate doc_id {doc_id}"
            continue
        seen_ids.add(doc_id)
        meta = {
            **doc.metadata,
            "source_path": str(path.relative_to(docs_dir)),
            "source_format": doc.source_format,
            "file_hash": file_hash,
        }
        documents[doc_id] = {
            k: meta[k]
            for k in ("title", "doc_type", "revision", "trust_level", "source_path", "file_hash")
            if k in meta
        }

        if doc_id in prev_docs and prev_docs[doc_id].get("file_hash") == file_hash and doc_id in prev_chunks:
            report.unchanged.append(doc_id)
            kept.extend(prev_chunks[doc_id])
            continue
        (report.updated if doc_id in prev_docs else report.added).append(doc_id)

        doc_codes = [str(c) for c in meta.get("alarm_codes") or []]
        for chunk in chunk_document(doc_id, doc.text, meta, max_chars=max_chars, overlap=overlap):
            # chunk-level alarm codes: the section that actually talks about the alarm ranks above the
            # rest of the document (e.g. SOP 4.2 for BFP_SP_LO rather than SOP 4.1)
            chunk.metadata["chunk_alarm_codes"] = [
                c for c in doc_codes if c in chunk.text or c in chunk.heading
            ]
            a = assess(chunk.text)
            chunk.metadata["injection_score"] = a.score
            chunk.metadata["injection_signals"] = list(a.signals)
            chunk.metadata["quarantined"] = a.flagged
            if a.flagged:
                report.flagged_chunks.append(chunk.chunk_id)
            kept.append((chunk, None))

    report.removed = sorted(set(prev_docs) - seen_ids)
    pending = [i for i, (_, v) in enumerate(kept) if v is None]
    if pending:
        vectors = embedder.embed([kept[i][0].embedding_text for i in pending])
        for j, i in enumerate(pending):
            kept[i] = (kept[i][0], vectors[j])
    chunks = [c for c, _ in kept]
    matrix = (
        np.vstack([v for _, v in kept if v is not None])
        if kept
        else np.zeros((0, embedder.dim), dtype=np.float32)
    )
    index = VectorIndex(chunks, matrix, VectorIndex.new_manifest(embedder.name, documents))
    index.save(index_dir)
    report.chunks_total = len(chunks)
    report.flagged_chunks = sorted({c.chunk_id for c in chunks if c.metadata.get("quarantined")})
    log.info("ingestion complete: %s", report.as_dict())
    return index, report
