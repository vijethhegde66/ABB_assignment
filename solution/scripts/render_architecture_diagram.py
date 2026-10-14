"""Render docs/architecture-diagram.png (requires matplotlib: pip install matplotlib)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "architecture-diagram.png"

C_UI, C_ORCH, C_MCP, C_SRC, C_RAG, C_OBS, C_SEC = (
    "#dbe8fb",
    "#e4dcf7",
    "#d6f0e0",
    "#fbe3d0",
    "#fff1c4",
    "#e8e8e8",
    "#f7d4d4",
)


def box(ax, x, y, w, h, title, lines=(), color="#ffffff", bold=True):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.15", fc=color, ec="#33415c", lw=1.2
        )
    )
    ax.text(
        x + w / 2,
        y + h - 0.28,
        title,
        ha="center",
        va="top",
        fontsize=9.5,
        fontweight="bold" if bold else "normal",
        color="#111827",
    )
    for i, line in enumerate(lines):
        ax.text(
            x + w / 2, y + h - 0.68 - i * 0.32, line, ha="center", va="top", fontsize=7.3, color="#374151"
        )
    return (x, y, w, h)


def arrow(ax, p, q, label="", color="#33415c", style="-|>", ls="-", lx=0.0, ly=0.0):
    ax.add_patch(
        FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=12, lw=1.3, color=color, linestyle=ls)
    )
    if label:
        ax.text(
            (p[0] + q[0]) / 2 + lx,
            (p[1] + q[1]) / 2 + ly,
            label,
            fontsize=7,
            color=color,
            ha="center",
            va="center",
            bbox={"fc": "white", "ec": "none", "pad": 0.6},
        )


def main() -> None:
    fig, ax = plt.subplots(figsize=(17, 11))
    ax.set_xlim(0, 34)
    ax.set_ylim(0, 22)
    ax.axis("off")
    ax.text(
        17,
        21.5,
        "Alarm Investigation & Procedure Guidance Copilot — architecture (MCP path + RAG path)",
        ha="center",
        fontsize=14,
        fontweight="bold",
    )

    # trust / auth boundaries
    ax.add_patch(
        FancyBboxPatch(
            (0.4, 0.4), 33.2, 20.4, boxstyle="round,pad=0.02", fc="none", ec="#9ca3af", lw=1, ls="--"
        )
    )
    ax.add_patch(
        FancyBboxPatch(
            (20.6, 9.7), 12.6, 10.4, boxstyle="round,pad=0.02", fc="none", ec="#b91c1c", lw=1.2, ls="--"
        )
    )
    ax.text(
        26.9,
        19.75,
        "Source-system boundary (bearer tokens, server-side secrets)",
        fontsize=7.5,
        color="#b91c1c",
        ha="center",
    )

    box(
        ax,
        1,
        16.6,
        6.2,
        3.3,
        "GUI (static SPA, nginx)",
        [
            "Chat · alarm summary · causes",
            "citations · MCP trace (raw req/resp)",
            "tool catalog · retrieval · demo faults",
            "CSP, output encoding",
        ],
        C_UI,
    )
    box(
        ax,
        8.6,
        13.2,
        10.6,
        6.7,
        "Copilot backend (FastAPI)",
        [
            "1 Intent detection + entity extraction",
            "2 Stage planner (intent -> MCP chain)",
            "3 Concurrent tool execution, output->input chaining",
            "4 Evidence fusion: causes, priority, related assets",
            "5 Consistency check: API recs vs controlled docs",
            "6 Grounded composer [T#]/[D#] (+ optional LLM",
            "   with citation & safety validation)",
            "Conversation memory · partial-failure handling",
        ],
        C_ORCH,
    )
    box(
        ax,
        14.1,
        9.6,
        5.1,
        3.0,
        "MCP client / tool registry",
        ["discovery (tools/list)", "JSON-schema arg validation", "timeouts · approval policy"],
        C_MCP,
    )
    box(
        ax,
        8.6,
        9.6,
        5.1,
        3.0,
        "Retrieval service",
        ["hybrid BM25 + vector (RRF)", "metadata filters · boosts", "confidence · quarantine"],
        C_RAG,
    )
    box(
        ax,
        21.2,
        15.6,
        5.6,
        4.0,
        "Alarm Management MCP server",
        [
            "14 typed read-only tools",
            "pydantic in/out schemas",
            "error mapping · trace meta",
            "Streamable HTTP + bearer / stdio",
        ],
        C_MCP,
    )
    box(
        ax,
        27.4,
        15.6,
        5.4,
        4.0,
        "Alarm API connector",
        [
            "httpx · auth header",
            "timeout · retry/backoff",
            "Retry-After · pagination",
            "trace_id/x-client-id",
        ],
        C_MCP,
    )
    box(
        ax,
        27.4,
        10.3,
        5.4,
        4.3,
        "Alarm Management API",
        [
            "(simulator, Postman contract)",
            "assets · alarms · summary",
            "trends · correlation · floods",
            "priority · recommendations · KPIs",
            "fault injection",
        ],
        C_SRC,
    )
    box(
        ax,
        21.2,
        10.3,
        5.6,
        4.3,
        "Work-orders MCP server",
        [
            "(optional secondary source)",
            "list_work_orders (read)",
            "draft -> submit (write)",
            "HMAC approval token,",
            "explicit user confirmation",
        ],
        C_SRC,
    )
    box(
        ax,
        1,
        4.6,
        6.6,
        4.6,
        "Document store",
        [
            "rag/documents (md · txt · html · pdf)",
            "SOPs, maintenance manuals,",
            "troubleshooting, safety,",
            "alarm philosophy, resolution notes",
            "(+ 1 poisoned vendor bulletin)",
        ],
        C_RAG,
    )
    box(
        ax,
        8.6,
        3.6,
        6.8,
        5.0,
        "RAG ingestion pipeline",
        [
            "extract (front matter / meta tags)",
            "injection screening -> quarantine",
            "heading-aware chunking + offsets",
            "metadata: doc/section/rev/codes",
            "embeddings (hashing | sentence-tf)",
            "incremental refresh (file hashes)",
        ],
        C_RAG,
    )
    box(
        ax,
        16.2,
        4.6,
        5.0,
        4.0,
        "Retrieval index",
        ["chunks.jsonl", "embeddings.npy", "manifest.json", "(swap: pgvector/Qdrant)"],
        C_RAG,
    )
    box(
        ax,
        22.4,
        1.2,
        10.6,
        3.2,
        "Observability",
        [
            "JSON logs with trace_id / request_id / conversation_id, tool, duration, status, retries,",
            "retrieval query, doc ids, scores, LLM latency — secrets redacted",
        ],
        C_OBS,
    )
    box(
        ax,
        1,
        10.4,
        6.6,
        4.4,
        "Optional LLM provider",
        [
            "LLM_PROVIDER=none (default):",
            "deterministic grounded composer",
            "LLM_PROVIDER=anthropic:",
            "claude-opus-5-5 writes narrative;",
            "rejected if citations/safety fail",
        ],
        C_SEC,
    )

    arrow(ax, (7.2, 18.2), (8.6, 18.2), "HTTPS /api/chat", lx=0, ly=0.35)
    arrow(ax, (11.1, 13.2), (11.1, 12.6))
    arrow(ax, (16.6, 13.2), (16.6, 12.6))
    arrow(
        ax, (19.2, 11.8), (21.2, 16.4), "MCP tools/call\n+ _meta trace_id", color="#047857", lx=-0.9, ly=0.2
    )
    arrow(ax, (19.2, 10.6), (21.2, 11.6), "", color="#047857")
    arrow(ax, (26.8, 17.6), (27.4, 17.6), color="#047857")
    arrow(ax, (30.1, 15.6), (30.1, 14.6), "HTTP + Bearer\n+ trace headers", color="#b45309", lx=1.9)
    arrow(ax, (7.6, 6.9), (8.6, 6.9))
    arrow(ax, (15.4, 6.6), (16.2, 6.6))
    arrow(ax, (17.0, 8.6), (12.8, 9.6), "query", color="#a16207", ly=-0.3)
    arrow(ax, (8.6, 15.0), (7.6, 13.0), "evidence pack", color="#7f1d1d", lx=-0.5, ly=0.3)
    arrow(ax, (19.2, 13.4), (22.4, 4.4), "logs", color="#6b7280", ls=":", lx=0.4)

    ax.text(
        1.0,
        1.9,
        "MCP path (green): copilot -> MCP client -> candidate MCP server -> connector -> Alarm API.\n"
        "RAG path (yellow): documents -> ingestion -> index -> retrieval -> same orchestration turn.\n"
        "Both paths converge in evidence fusion; every answer statement cites [T#] tool calls or [D#] passages.",
        fontsize=8,
        color="#111827",
        va="center",
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=140, bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
