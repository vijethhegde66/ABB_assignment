"""Text + metadata extraction for Markdown, plain text, HTML and PDF documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

import yaml

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}
LIST_FIELDS = ("equipment_classes", "alarm_codes", "sites")


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedDocument:
    path: Path
    text: str  # normalised, Markdown-like (headings preserved as '#' lines)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_format: str = "markdown"


_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    m = _FRONT_MATTER.match(raw)
    if not m:
        return {}, raw
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ExtractionError("front matter must be a mapping")
    return meta, raw[m.end() :]


def _normalise_meta(meta: dict[str, Any], path: Path) -> dict[str, Any]:
    out = {str(k): v for k, v in meta.items()}
    for key in LIST_FIELDS:
        val = out.get(key)
        if isinstance(val, str):
            out[key] = [v.strip() for v in val.split(",") if v.strip()]
        elif val is None:
            out[key] = []
        else:
            out[key] = [str(v) for v in val]
    out.setdefault("doc_id", path.stem.split("_")[0])
    out.setdefault("title", path.stem.replace("_", " "))
    out.setdefault("doc_type", "unknown")
    out.setdefault("trust_level", "uncontrolled")
    for key in ("revision", "effective_date"):
        if key in out and out[key] is not None:
            out[key] = str(out[key])
    return out


class _HtmlToText(HTMLParser):
    """Converts HTML to Markdown-like text; drops <script>/<style>; captures <meta name=... content=...>."""

    BLOCK: ClassVar[set[str]] = {"p", "div", "section", "article", "table", "ul", "ol", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._skip = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
        elif tag == "meta" and a.get("name") and a.get("content") is not None:
            self.meta[a["name"] or ""] = a["content"] or ""
        elif tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._skip = max(0, self._skip - 1)
        elif tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.parts.append("\n| " + " | ".join(self._row) + " |")
            self._row = None
        elif tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self.parts and self.parts[-1].startswith("\n") and self.parts[-1].strip().startswith("#"):
            self.parts[-1] += data.strip()
        else:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    meta: dict[str, Any] = {}
    info: Any = reader.metadata or {}
    if info.get("/Title"):
        meta["title"] = str(info["/Title"])
    sidecar = path.with_suffix(".meta.yaml")
    if sidecar.exists():
        meta.update(yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {})
    text = "\n\n".join(f"<!-- page {i + 1} -->\n{t}" for i, t in enumerate(pages) if t)
    return text, meta


def extract(path: Path) -> ExtractedDocument:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ExtractionError(f"unsupported file type: {suffix}")
    if suffix == ".pdf":
        text, meta = _extract_pdf(path)
        fmt = "pdf"
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            parser = _HtmlToText()
            parser.feed(raw)
            text, meta, fmt = parser.text(), dict(parser.meta), "html"
            if "title" not in meta:
                m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
                if m:
                    meta["title"] = m.group(1).strip()
        else:
            meta, text = _split_front_matter(raw)
            fmt = "markdown" if suffix in {".md", ".markdown"} else "text"
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ExtractionError(f"no extractable text in {path.name}")
    return ExtractedDocument(path=path, text=text, metadata=_normalise_meta(meta, path), source_format=fmt)
