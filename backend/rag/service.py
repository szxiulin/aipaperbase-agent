from __future__ import annotations

import sqlite3
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


def ingest_parsed(
    catalog_conn: sqlite3.Connection,
    parse_conn: sqlite3.Connection,
    entity_ids: list[str],
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Chunk the Markdown of parsed papers, embed them in batches, and write them into the vector store."""
    pipeline = get_pipeline()
    documents: list[Document] = []
    skipped = 0
    for entity_id in dict.fromkeys(entity_ids):
        if not parse_store.is_parsed(parse_conn, entity_id):
            skipped += 1
            continue
        path = parse_store.parsed_path(entity_id)
        if not path.exists():
            skipped += 1
            continue
        documents.append(Document(
            id=entity_id,
            text=path.read_text(encoding="utf-8"),
            metadata={"entity_id": entity_id, "title": _entity_title(catalog_conn, entity_id)},
        ))
    total = len(documents)
    chunk_count = 0
    processed = 0
    batch_size = 10
    while processed < total:
        if is_cancelled and is_cancelled():
            break
        batch = documents[processed : processed + batch_size]
        chunk_count += pipeline.ingest(batch)
        processed += len(batch)
        if on_progress:
            on_progress({
                "total": total, "processed": processed,
                "ingested_chunks": chunk_count, "documents": total, "skipped": skipped,
            })
    return {"ingested_chunks": chunk_count, "documents": total, "skipped": skipped}


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
    try:
        pipeline = get_pipeline()
    except Exception:
        return set()
    method = getattr(pipeline.store, "document_ids", None)
    return method() if method else set()
