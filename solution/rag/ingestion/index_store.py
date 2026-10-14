"""File-based vector index: ``chunks.jsonl`` + ``embeddings.npy`` + ``manifest.json``.

Small, transparent and versionable; the ``VectorIndex`` interface is what the
retriever depends on, so it can be swapped for pgvector / Qdrant / OpenSearch
without touching retrieval or orchestration code.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from rag.ingestion.chunker import Chunk

INDEX_VERSION = 2


class IndexNotFoundError(FileNotFoundError):
    pass


class VectorIndex:
    def __init__(self, chunks: list[Chunk], embeddings: np.ndarray, manifest: dict[str, Any]) -> None:
        if len(chunks) != embeddings.shape[0]:
            raise ValueError("chunks and embeddings length mismatch")
        self.chunks = chunks
        self.embeddings = embeddings.astype(np.float32)
        self.manifest = manifest

    @property
    def embedder_name(self) -> str:
        return str(self.manifest.get("embedder", ""))

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # write atomically: build in a temp dir next to the target, then rename files into place
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            t = Path(tmp)
            with (t / "chunks.jsonl").open("w", encoding="utf-8") as fh:
                for c in self.chunks:
                    fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
            np.save(t / "embeddings.npy", self.embeddings)
            (t / "manifest.json").write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
            for name in ("chunks.jsonl", "embeddings.npy", "manifest.json"):
                os.replace(t / name, directory / name)

    @classmethod
    def load(cls, directory: Path) -> VectorIndex:
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise IndexNotFoundError(f"no retrieval index at {directory}; run the ingestion command first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("index_version") != INDEX_VERSION:
            raise ValueError("index version mismatch; rebuild the index")
        chunks = []
        with (directory / "chunks.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    chunks.append(Chunk(**json.loads(line)))
        embeddings = np.load(directory / "embeddings.npy")
        return cls(chunks, embeddings, manifest)

    @staticmethod
    def new_manifest(embedder: str, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "index_version": INDEX_VERSION,
            "embedder": embedder,
            "built_at": datetime.now(UTC).isoformat(),
            "documents": documents,
        }
