from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rag.ingestion.embeddings import HashingEmbedder
from rag.ingestion.pipeline import ingest
from rag.retrieval.retriever import HybridRetriever

DOCS = Path(__file__).resolve().parents[1] / "documents"


@pytest.fixture(scope="session")
def built_index(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("index")
    index, report = ingest(DOCS, out, HashingEmbedder())
    return index, report, out


@pytest.fixture(scope="session")
def retriever(built_index) -> HybridRetriever:
    return HybridRetriever(built_index[0], HashingEmbedder())


@pytest.fixture
def docs_copy(tmp_path: Path) -> Path:
    target = tmp_path / "docs"
    shutil.copytree(DOCS, target)
    return target


def minimal_pdf(text: str) -> bytes:
    """Build a tiny valid single-page PDF containing ``text`` (no PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
