"""`knowledge` MCP server: BM25 retriever over the markdown policy docs in data/docs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from rank_bm25 import BM25Okapi

from agent.config import settings
from agent.tracing import traced_tool


@dataclass
class Chunk:
    doc_id: str
    title: str
    heading: str
    text: str


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


def _parse(path: Path) -> tuple[str, str, str]:
    raw = path.read_text()
    doc_id, title = path.stem, path.stem
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "doc_id":
                doc_id = v.strip()
            elif k.strip() == "title":
                title = v.strip()
        raw = raw[m.end():]
    return doc_id, title, raw


class DocIndex:
    def __init__(self, docs_dir: Path):
        self.docs: dict[str, tuple[str, str]] = {}
        self.chunks: list[Chunk] = []
        for path in sorted(docs_dir.glob("*.md")):
            doc_id, title, body = _parse(path)
            self.docs[doc_id] = (title, body)
            heading = title
            buf: list[str] = []
            for line in body.splitlines():
                if line.startswith("#"):
                    if buf and "".join(buf).strip():
                        self.chunks.append(Chunk(doc_id, title, heading, "\n".join(buf).strip()))
                    heading = line.lstrip("# ").strip()
                    buf = []
                else:
                    buf.append(line)
            if buf and "".join(buf).strip():
                self.chunks.append(Chunk(doc_id, title, heading, "\n".join(buf).strip()))
        corpus = [_tokens(f"{c.title} {c.heading} {c.text}") for c in self.chunks]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(_tokens(query))
        ranked = sorted(zip(self.chunks, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked[:k] if s > 0]


_index: DocIndex | None = None


def get_index() -> DocIndex:
    global _index
    if _index is None:
        _index = DocIndex(settings.docs_dir)
    return _index


def _text(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["is_error"] = True
    return out


@tool(
    "search_docs",
    "Search the loyalty program rules, campaign measurement playbook, segment definitions and KPI glossary. "
    "Returns the most relevant passages with doc ids.",
    {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 3}}, "required": ["query"]},
)
@traced_tool("search_docs", as_type="retriever")
async def search_docs(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    k = max(1, min(int(args.get("k") or 3), 8))
    hits = get_index().search(query, k)
    if not hits:
        return _text(f"No passages matched '{query}'. Available docs: {', '.join(get_index().docs)}")
    parts = [f"### [{c.doc_id}] {c.title} › {c.heading} (score {s:.2f})\n{c.text}" for c, s in hits]
    return _text("\n\n".join(parts))


@tool("get_doc", "Fetch a full document by doc_id (see search_docs results).",
      {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]})
@traced_tool("get_doc", as_type="retriever")
async def get_doc(args: dict[str, Any]) -> dict[str, Any]:
    doc = get_index().docs.get(str(args.get("doc_id", "")))
    if not doc:
        return _text(f"Unknown doc_id. Available: {', '.join(get_index().docs)}", is_error=True)
    return _text(f"# {doc[0]}\n{doc[1]}")


SERVER = create_sdk_mcp_server(name="knowledge", version="1.0.0", tools=[search_docs, get_doc])
