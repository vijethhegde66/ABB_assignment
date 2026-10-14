"""Build / refresh the retrieval index.

python -m rag.ingestion.cli --docs rag/documents --index var/rag-index [--rebuild]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rag.ingestion.embeddings import get_embedder
from rag.ingestion.pipeline import ingest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into the RAG retrieval index")
    parser.add_argument("--docs", default=os.getenv("DOCUMENT_PATH", "rag/documents"))
    parser.add_argument("--index", default=os.getenv("RAG_INDEX_PATH", "var/rag-index"))
    parser.add_argument("--embedding-provider", default=os.getenv("EMBEDDING_PROVIDER", "hashing"))
    parser.add_argument(
        "--rebuild", action="store_true", help="ignore the existing index and rebuild everything"
    )
    args = parser.parse_args(argv)

    docs = Path(args.docs)
    if not docs.is_dir():
        print(f"document directory not found: {docs}", file=sys.stderr)
        return 2
    index, report = ingest(
        docs, Path(args.index), get_embedder(args.embedding_provider), rebuild=args.rebuild
    )
    print(
        json.dumps({"index": str(args.index), "embedder": index.embedder_name, **report.as_dict()}, indent=2)
    )
    return 1 if report.failed and not index.chunks else 0


if __name__ == "__main__":
    raise SystemExit(main())
