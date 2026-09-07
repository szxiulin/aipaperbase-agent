"""Small coordinator for the confirmed download -> parse -> vector ingest flow."""
from __future__ import annotations

from typing import Any, Callable


def new_result(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requested": len(items), "available": 0, "downloaded": 0, "parsed": 0,
        "ingested_documents": 0, "ingested_chunks": 0, "already_indexed": 0,
        "unavailable": 0, "failed": 0, "items": items,
    }


def completion_message(result: dict[str, Any]) -> str:
    indexed = int(result.get("ingested_documents", 0)) + int(result.get("already_indexed", 0))
    summary = (f"请求 {result.get('requested', 0)} 篇：下载 {result.get('downloaded', 0)}，"
               f"解析 {result.get('parsed', 0)}，新增向量论文 {result.get('ingested_documents', 0)}，"
               f"向量 chunk {result.get('ingested_chunks', 0)}。")
    if indexed:
        conclusion = "部分论文已可进行全文问答。" if indexed < result.get("requested", 0) else "论文已可进行全文问答。"
    else:
        conclusion = "没有论文完成全文向量入库，暂不能进行全文问答。"
    blocked = []
    for item in result.get("items", []):
        if item.get("status") in {"unavailable", "failed"}:
            title = item.get("title") or item.get("canonical_entity_id") or "论文"
            blocked.append(f"《{title}》：{item.get('message') or '未完成入库。'}")
    return summary + conclusion + (" 未完成项：" + "；".join(blocked) if blocked else "")


def parseable_ids_after_pdf_dedup(download_conn, items: list[dict[str, Any]]) -> list[str]:
    """Exclude cross-entity PDF duplicates before MinerU/vector work, preserving a visible item."""
    ids: list[str] = []
    for item in items:
        entity_id = item["canonical_entity_id"]
        row = download_conn.execute(
            "SELECT status, duplicate_of FROM downloads WHERE entity_id=?", (entity_id,)
        ).fetchone()
        if row and row["status"] == "duplicate" and row["duplicate_of"]:
            item.update(downloadable=False, status="ambiguous_duplicate", stage="pdf",
                        reason_code="duplicate_entity", message="PDF 与另一论文实体相同，未自动合并或重复向量化。")
        else:
            ids.append(entity_id)
    return ids


def finalize(items: list[dict[str, Any]], download: dict[str, Any], parse: dict[str, Any], ingest: dict[str, Any]) -> dict[str, Any]:
    """Merge stage results into the stable public result contract.

    Stage implementations may expose more fields; only documented fields leave
    this coordinator, preventing another accidental key-name contract drift.
    """
    result = new_result(items)
    result["available"] = sum(bool(item.get("downloadable")) for item in items)
    result["downloaded"] = int(download.get("success", 0))
    result["parsed"] = int(parse.get("parsed", 0))
    result["ingested_documents"] = int(ingest.get("documents", 0))
    result["ingested_chunks"] = int(ingest.get("ingested_chunks", 0))
    result["already_indexed"] = int(ingest.get("already_indexed", 0))
    for item in items:
        if not item.get("downloadable"):
            item.update(status="unavailable", stage="source", chunk_count=0,
                        reason_code=item.get("reason_code") or "no_download_source",
                        message=item.get("message") or "未找到可信的开放 PDF 来源。")
            result["unavailable"] += 1
        elif item.get("canonical_entity_id") in set(ingest.get("indexed_ids", [])):
            item.update(status="indexed_current", stage="complete", chunk_count=ingest.get("chunk_counts", {}).get(item["canonical_entity_id"], 0), reason_code="", message="")
        elif item.get("canonical_entity_id") in set(ingest.get("already_indexed_ids", [])):
            item.update(status="indexed_current", stage="already_indexed", chunk_count=ingest.get("chunk_counts", {}).get(item["canonical_entity_id"], 0), reason_code="", message="索引内容与当前正文和管线一致，已复用。")
        else:
            item.update(status="failed", stage=ingest.get("failed_stage", "ingest"), chunk_count=0,
                        reason_code=ingest.get("reason_code", "embedding_failed"),
                        message=ingest.get("message", "未完成向量入库。"))
            result["failed"] += 1
    return result
