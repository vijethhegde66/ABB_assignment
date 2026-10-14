"""Document ingestion: extraction, metadata capture, chunking and incremental index refresh."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from rag.ingestion.chunker import chunk_document
from rag.ingestion.embeddings import HashingEmbedder
from rag.ingestion.extractors import ExtractionError, extract
from rag.ingestion.index_store import IndexNotFoundError, VectorIndex
from rag.ingestion.pipeline import ingest
from rag.tests.conftest import DOCS, minimal_pdf


def test_markdown_front_matter_metadata() -> None:
    doc = extract(DOCS / "SOP-BFP-001_boiler_feed_pump_operation.md")
    m = doc.metadata
    assert m["doc_id"] == "SOP-BFP-001" and m["doc_type"] == "operating_procedure"
    assert m["revision"] == "7" and m["trust_level"] == "controlled"
    assert "BFP_SP_LO" in m["alarm_codes"] and "boiler_feed_pump" in m["equipment_classes"]
    assert not doc.text.startswith("---") and doc.text.startswith("# Boiler Feed Pump")


def test_html_extraction_strips_scripts_and_keeps_structure() -> None:
    doc = extract(DOCS / "ALM-PHIL-030_alarm_management_philosophy.html")
    assert doc.source_format == "html" and doc.metadata["doc_id"] == "ALM-PHIL-030"
    assert doc.metadata["sites"] == ["NorthPlant", "SouthPlant", "EastRefinery"]
    assert "console.log" not in doc.text and "font-family" not in doc.text
    assert "## 2. Alarm Priority and Response Time" in doc.text
    assert "| P1 / Urgent | critical |" in doc.text
    assert "- Average alarm rate" in doc.text


def test_plain_text_with_front_matter() -> None:
    doc = extract(DOCS / "RN-2026-014_bfp101_incident_resolution_notes.txt")
    assert doc.source_format == "text" and doc.metadata["doc_type"] == "resolution_notes"
    assert "LCV-100 positioner" in doc.text


def test_pdf_extraction_with_sidecar_metadata(tmp_path: Path) -> None:
    pdf = tmp_path / "MAN-TEST-001_manual.pdf"
    pdf.write_bytes(minimal_pdf("Lube oil filter changeover at 0.8 bar differential pressure"))
    (tmp_path / "MAN-TEST-001_manual.meta.yaml").write_text(
        "doc_id: MAN-TEST-001\ndoc_type: maintenance_manual\ntrust_level: controlled\n"
    )
    doc = extract(pdf)
    assert doc.source_format == "pdf" and doc.metadata["doc_id"] == "MAN-TEST-001"
    assert "Lube oil filter changeover" in doc.text


def test_unsupported_and_empty_documents(tmp_path: Path) -> None:
    (tmp_path / "x.docx").write_text("nope")
    with pytest.raises(ExtractionError):
        extract(tmp_path / "x.docx")
    (tmp_path / "empty.md").write_text("---\ndoc_id: E\n---\n   \n")
    with pytest.raises(ExtractionError):
        extract(tmp_path / "empty.md")


def test_chunking_is_heading_aware_with_offsets() -> None:
    doc = extract(DOCS / "SOP-BFP-001_boiler_feed_pump_operation.md")
    chunks = chunk_document("SOP-BFP-001", doc.text, doc.metadata)
    headings = [c.heading for c in chunks]
    assert "4.2 BFP Suction Pressure Low (BFP_SP_LO)" in headings
    sp = next(c for c in chunks if c.heading.startswith("4.2"))
    assert sp.section_number == "4.2"
    assert sp.heading_path[-2:] == ["4. Alarm Response", "4.2 BFP Suction Pressure Low (BFP_SP_LO)"]
    assert "4.1 BFP Discharge" not in sp.text  # never straddles sections
    assert doc.text[sp.char_start : sp.char_end].strip() == sp.text
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert sp.embedding_text.startswith("Boiler Feed Pump Operation and Alarm Response Procedure > ")


def test_long_sections_split_with_overlap() -> None:
    body = " ".join(f"Sentence number {i} explains a maintenance step in detail." for i in range(80))
    chunks = chunk_document(
        "D", f"# Title\n\n## 1. Long\n\n{body}", {"title": "T"}, max_chars=400, overlap=80
    )
    assert len(chunks) > 3 and all(len(c.text) <= 400 for c in chunks)
    for a, b in itertools.pairwise(chunks):
        assert b.char_start < a.char_end  # overlapping windows


def test_ingest_report_metadata_and_quarantine(built_index) -> None:
    index, report, _ = built_index
    assert report.documents_total == 11 and not report.failed and len(report.added) == 11
    assert report.chunks_total == len(index.chunks) > 40
    assert report.flagged_chunks == ["KB-VENDOR-099#001-2-notes-for-operators"]
    chunk = next(
        c for c in index.chunks if c.chunk_id.startswith("SOP-BFP-001") and c.heading.startswith("4.2")
    )
    assert chunk.metadata["chunk_alarm_codes"] == ["BFP_SP_LO"]
    assert chunk.metadata["source_path"] == "SOP-BFP-001_boiler_feed_pump_operation.md"
    assert index.embeddings.shape == (len(index.chunks), 1024)


def test_incremental_refresh(docs_copy: Path, tmp_path: Path) -> None:
    out = tmp_path / "idx"
    emb = HashingEmbedder()
    ingest(docs_copy, out, emb)
    _, second = ingest(docs_copy, out, emb)
    assert len(second.unchanged) == 11 and not second.added and not second.updated
    target = docs_copy / "MM-MTR-021_induction_motor_maintenance.md"
    target.write_text(target.read_text() + "\n## 6. New Section\n\nRe-torque terminal box bolts annually.\n")
    (docs_copy / "SAF-GEN-040_general_process_safety.md").unlink()
    index, third = ingest(docs_copy, out, emb)
    assert third.updated == ["MM-MTR-021"] and third.removed == ["SAF-GEN-040"]
    assert not any(c.doc_id == "SAF-GEN-040" for c in index.chunks)
    assert any("Re-torque" in c.text for c in index.chunks)
    assert VectorIndex.load(out).manifest["documents"]["MM-MTR-021"]["file_hash"]


def test_missing_index_raises(tmp_path: Path) -> None:
    with pytest.raises(IndexNotFoundError):
        VectorIndex.load(tmp_path / "none")


def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder(dim=256)
    a, b = e.embed(["lube oil pressure low", "lube oil pressure low"])
    assert (a == b).all() and abs(float((a * a).sum()) - 1.0) < 1e-5
    sim_related = float(e.embed(["lubrication oil pressure"])[0] @ a)
    sim_unrelated = float(e.embed(["reactor bed temperature"])[0] @ a)
    assert sim_related > sim_unrelated
