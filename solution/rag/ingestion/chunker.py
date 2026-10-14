"""Heading-aware chunking.

Documents are split on Markdown headings first so a chunk never straddles two
sections (procedure steps stay together with their heading). Sections longer
than ``max_chars`` are split on paragraph/sentence boundaries with overlap.
Every chunk records its heading path, section number and character offsets so
citations can point to an exact section.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
_SECTION_NO = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n{2,}|\n(?=\d+\.\s|- )")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    heading: str
    heading_path: list[str]
    section_number: str | None
    char_start: int
    char_end: int
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def embedding_text(self) -> str:
        """Contextual header + body: improves retrieval of short procedure steps."""
        title = str(self.metadata.get("title", ""))
        return f"{title} > {' > '.join(self.heading_path)}\n{self.text}"


@dataclass
class _Section:
    level: int
    heading: str
    start: int
    end: int
    path: list[str]


def _sections(text: str) -> list[_Section]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [_Section(1, "Document", 0, len(text), ["Document"])]
    out: list[_Section] = []
    stack: list[tuple[int, str]] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        out.append(_Section(1, "Preamble", 0, matches[0].start(), ["Preamble"]))
    for i, m in enumerate(matches):
        level, heading = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append(_Section(level, heading, m.start(), end, [h for _, h in stack]))
    return out


def _split_long(body: str, max_chars: int, overlap: int) -> list[tuple[int, int]]:
    """Return (start, end) offsets within ``body`` respecting sentence boundaries."""
    if len(body) <= max_chars:
        return [(0, len(body))]
    bounds = [0] + [m.end() for m in _SENTENCE_END.finditer(body)] + [len(body)]
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(body):
        end = start + max_chars
        if end >= len(body):
            spans.append((start, len(body)))
            break
        candidates = [b for b in bounds if start + max_chars // 2 < b <= end]
        cut = candidates[-1] if candidates else end
        spans.append((start, cut))
        back = [b for b in bounds if cut - overlap <= b < cut]
        start = back[0] if back else max(cut - overlap, start + 1)
    return spans


def chunk_document(
    doc_id: str,
    text: str,
    metadata: dict[str, Any],
    max_chars: int = 1100,
    overlap: int = 150,
    min_chars: int = 15,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sec in _sections(text):
        raw = text[sec.start : sec.end]
        # body without the heading line itself
        first_nl = raw.find("\n")
        is_heading = raw.lstrip().startswith("#")
        body_offset = sec.start + (first_nl + 1 if is_heading and first_nl >= 0 else 0)
        body = text[body_offset : sec.end]
        if len(body.strip()) < min_chars:
            continue  # heading-only section (its children carry the content)
        m = _SECTION_NO.match(sec.heading)
        section_number = m.group(1) if m else None
        for s, e in _split_long(body, max_chars, overlap):
            piece = body[s:e].strip()
            if len(piece) < min_chars:
                continue
            ordinal = len(chunks)
            slug = re.sub(r"[^a-z0-9]+", "-", sec.heading.lower()).strip("-")[:40] or "section"
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{ordinal:03d}-{slug}",
                    doc_id=doc_id,
                    ordinal=ordinal,
                    text=piece,
                    heading=sec.heading,
                    heading_path=sec.path,
                    section_number=section_number,
                    char_start=body_offset + s,
                    char_end=body_offset + e,
                    content_hash=hashlib.sha256(piece.encode()).hexdigest()[:16],
                    metadata=dict(metadata),
                )
            )
    return chunks
