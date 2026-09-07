from __future__ import annotations

import sqlite3
import hashlib
import json
import threading
from types import SimpleNamespace
from typing import Any, Callable

from raglib import (
    Chunk,
    Document,
    MarkdownSplitter,
    OpenAICompatEmbedder,
    OpenAICompatGenerator,
    OpenAICompatReranker,
    Pipeline,
    QdrantStore,
)
from raglib.config import load_config, load_dotenv

from backend.rag import evidence, parse_store


_pipeline: Pipeline | None = None
_entity_locks: dict[str, threading.Lock] = {}
_entity_locks_guard = threading.Lock()


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        load_dotenv()
        _pipeline = build_pipeline(load_config())
    return _pipeline


def reset_pipeline() -> None:
    global _pipeline
    _pipeline = None


def build_pipeline(config) -> Pipeline:
    if not config.embedder.base_url or not config.embedder.model:
        raise ValueError("缺少 Embedding 配置（EMBEDDING_BASE_URL / EMBEDDING_MODEL），请检查 .env")
    if not config.embedder.dim:
        raise ValueError("缺少 EMBEDDING_DIM 配置，请检查 .env")
    embedder = OpenAICompatEmbedder(
        config.embedder.base_url, config.embedder.api_key, config.embedder.model, config.embedder.dim
    )
    reranker = (
        OpenAICompatReranker(config.reranker.base_url, config.reranker.api_key, config.reranker.model)
        if config.reranker.model else None
    )
    generator = (
        OpenAICompatGenerator(
            config.generator.base_url, config.generator.api_key, config.generator.model,
            thinking=config.generator.thinking,
            reasoning_effort=config.generator.reasoning_effort,
            temperature=config.generator.temperature,
            top_p=config.generator.top_p,
            max_tokens=config.generator.max_tokens,
        )
        if config.generator.model else None
    )
    store = QdrantStore(config.qdrant.url or "http://127.0.0.1:6333", config.qdrant.collection, config.embedder.dim)
    return Pipeline(MarkdownSplitter(), embedder, store, reranker, generator)


def _entity_title(catalog_conn: sqlite3.Connection, entity_id: str) -> str:
    row = catalog_conn.execute(
        "SELECT canonical_title FROM paper_entities WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    return row["canonical_title"] if row else ""


def _lock_for(entity_id: str) -> threading.Lock:
    with _entity_locks_guard:
        return _entity_locks.setdefault(entity_id, threading.Lock())


def pipeline_fingerprint(pipeline: Pipeline) -> str:
    """Stable, non-secret fingerprint of the output-affecting index pipeline."""
    splitter = pipeline.splitter
    store = pipeline.store
    payload = {
        "splitter": f"{type(splitter).__module__}.{type(splitter).__name__}",
        "max_chars": getattr(splitter, "max_chars", None),
        "embedding_model": getattr(pipeline.embedder, "model", ""),
        "embedding_dim": getattr(pipeline.embedder, "dim", 0),
        "collection": getattr(store, "collection", type(store).__name__),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def ingest_parsed(
    catalog_conn: sqlite3.Connection,
    parse_conn: sqlite3.Connection,
    entity_ids: list[str],
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    canonical_ids: dict[str, str] | None = None,
    pipeline: Pipeline | None = None,
) -> dict[str, Any]:
    """Chunk the Markdown of parsed papers, embed them in batches, and write them into the vector store."""
    pipeline = pipeline or get_pipeline()
    canonical_ids = canonical_ids or {}
    fingerprint = pipeline_fingerprint(pipeline)
    documents: list[tuple[str, str, Document, str]] = []
    skipped = 0
    for entity_id in dict.fromkeys(entity_ids):
        if not parse_store.is_parsed(parse_conn, entity_id):
            skipped += 1
            continue
        path = parse_store.parsed_path(entity_id)
        if not path.exists():
            skipped += 1
            continue
        canonical_id = canonical_ids.get(entity_id, entity_id)
        text = path.read_text(encoding="utf-8")
        parsed_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        documents.append((entity_id, canonical_id, Document(
            id=canonical_id, text=text,
            metadata={"entity_id": canonical_id, "title": _entity_title(catalog_conn, canonical_id)},
        ), parsed_sha256))
    total = len(documents)
    chunk_count = 0
    processed = 0
    indexed_ids: list[str] = []
    already_indexed_ids: list[str] = []
    stale_ids: list[str] = []
    chunk_counts: dict[str, int] = {}
    while processed < total:
        if is_cancelled and is_cancelled():
            break
        requested_id, canonical_id, document, parsed_sha256 = documents[processed]
        with _lock_for(canonical_id):
            old_chunks = pipeline.store.list_by_document(canonical_id)
            state = parse_store.index_state(parse_conn, requested_id)
            current = (state["parsed_sha256"] == parsed_sha256
                       and state["indexed_parsed_sha256"] == parsed_sha256
                       and state["indexed_pipeline_fingerprint"] == fingerprint
                       and state["indexed_chunk_count"] > 0
                       and len(old_chunks) == state["indexed_chunk_count"])
            if current:
                already_indexed_ids.append(canonical_id)
                chunk_counts[canonical_id] = len(old_chunks)
            else:
                if state["indexed_pipeline_fingerprint"] or state["indexed_parsed_sha256"]:
                    stale_ids.append(canonical_id)
                written = pipeline.ingest([document])
                chunk_count += written
                chunk_counts[canonical_id] = written
                parse_store.set_index_state(parse_conn, requested_id, parsed_sha256=parsed_sha256,
                                            pipeline_fingerprint=fingerprint, chunk_count=written)
                indexed_ids.append(canonical_id)
        processed += 1
        if on_progress:
            on_progress({
                "total": total, "processed": processed,
                "ingested_chunks": chunk_count, "documents": len(indexed_ids), "skipped": skipped,
                "already_indexed": len(already_indexed_ids),
            })
    return {
        "ingested_chunks": chunk_count, "documents": len(indexed_ids), "skipped": skipped,
        "already_indexed": len(already_indexed_ids), "indexed_ids": indexed_ids,
        "already_indexed_ids": already_indexed_ids, "stale_ids": stale_ids, "chunk_counts": chunk_counts,
    }


def query(text: str, top_k: int = 5, filters: dict | None = None, history: list[dict] | None = None) -> dict[str, Any]:
    result = get_pipeline().query(text, top_k, filters, history=history)
    return {
        "answer": result["answer"],
        "chunks": [evidence.chunk_citation(chunk) for chunk in result["chunks"]],
        "finish_reason": result.get("finish_reason", "stop"),
        "reasoning": result.get("reasoning", ""),
        "model": result.get("model", ""),
    }


def chat_query(search_query: str, top_k: int = 5, history: list[dict] | None = None) -> dict[str, Any]:
    """Default implementation for chats.ask: one retrieval (with relevance scores) + generation with history.

    Returns {"answer", "chunks", "model", "finish_reason", "reasoning"}.
    An empty answer with 0 hits is normal; when evidence exists but the generator returns empty
    (blank content), raise a friendly error so the caller marks this round as failed and can retry,
    rather than treating it as "generator not configured".
    """
    pipeline = get_pipeline()
    hits = pipeline.retriever.retrieve_scored(search_query, top_k)
    chunks = [chunk for chunk, _ in hits]
    finish_reason = "stop"
    reasoning = ""
    model = ""
    answer = ""
    if chunks and pipeline.generator:
        result = pipeline.generator.generate(search_query, chunks, history)
        answer = result.get("answer") or ""
        finish_reason = result.get("finish_reason") or "stop"
        reasoning = result.get("reasoning") or ""
        model = result.get("model") or ""
        if not answer.strip() and finish_reason != "length":
            raise ValueError("生成模型未返回内容（可能命中内容过滤或限流），请重试")
    return {
        "answer": answer,
        "chunks": [evidence.chunk_citation(chunk, score=score) for chunk, score in hits],
        "model": model,
        "finish_reason": finish_reason,
        "reasoning": reasoning,
    }


def regenerate_answer(query: str, chunks: list[Chunk], history: list[dict] | None = None) -> dict:
    """Default implementation for chats.regenerate: re-invoke the generator with the stored evidence chunks (no re-retrieval)."""
    pipeline = get_pipeline()
    if pipeline.generator is None:
        raise ValueError("未配置生成模型（GENERATOR_*），无法重新生成")
    result = pipeline.generator.generate(query, chunks, history)
    if not (result.get("answer") or "").strip() and result.get("finish_reason") != "length":
        raise ValueError("生成模型未返回内容（可能命中内容过滤或限流），请重试")
    return result


def delete_ingested(entity_id: str) -> dict[str, Any]:
    """Delete all chunks of a paper from the vector store."""
    try:
        pipeline = get_pipeline()
    except Exception:
        return {"entity_id": entity_id, "removed": False}
    method = getattr(pipeline.store, "delete", None)
    if method is None:
        return {"entity_id": entity_id, "removed": False}
    method(entity_id)
    return {"entity_id": entity_id, "removed": True}


def ingested_document_ids() -> set[str]:
    """The set of document_ids already ingested into the vector store (for showing ingest status)."""
    counts, _ = index_reconcile_snapshot()
    return {key for key, count in (counts or {}).items() if count > 0}


def index_reconcile_snapshot() -> tuple[dict[str, int] | None, str]:
    """Read actual vector counts and the active pipeline identity for reconciliation.

    ``None`` means Qdrant/pipeline is unavailable; callers must represent that
    state explicitly instead of turning it into an empty index.
    """
    try:
        # A status page must never create a Qdrant collection.  Reuse a live
        # pipeline if it already exists; otherwise build a read-only store.
        if _pipeline is not None:
            pipeline = _pipeline
            method = getattr(pipeline.store, "document_chunk_counts", None)
            return (method() if method else None), pipeline_fingerprint(pipeline)
        load_dotenv()
        config = load_config()
        if not config.qdrant.url:
            return None, ""
        store = QdrantStore(config.qdrant.url or "http://127.0.0.1:6333", config.qdrant.collection,
                            config.embedder.dim, create_if_missing=False)
        probe = SimpleNamespace(splitter=MarkdownSplitter(), store=store,
                                embedder=SimpleNamespace(model=config.embedder.model, dim=config.embedder.dim))
        return store.document_chunk_counts(), pipeline_fingerprint(probe)
    except Exception:
        return None, ""
