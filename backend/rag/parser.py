from __future__ import annotations

import sqlite3
from typing import Any, Callable

from backend.library import store as download_store
from backend.rag import loader, parse_store


def run_parse(
    download_conn: sqlite3.Connection,
    parse_conn: sqlite3.Connection,
    entity_ids: list[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """Parse downloaded PDFs into Markdown + images in batch, writing to disk and recording status."""
    ids = list(dict.fromkeys(entity_ids))
    total = len(ids)
    results = {"parsed": 0, "failed": 0, "skipped": 0, "no_pdf": 0}

    to_process: list[str] = []
    for entity_id in ids:
        if not download_store.is_success(download_conn, entity_id):
            results["no_pdf"] += 1
        elif parse_store.is_parsed(parse_conn, entity_id):
            results["skipped"] += 1
        else:
            to_process.append(entity_id)

    processed = results["skipped"]
    if on_progress:
        on_progress({"total": total, "processed": processed, "current": "", **results})

    if not to_process:
        return {"total": total, **results}

    pdf_paths = [download_store.paper_path(entity_id) for entity_id in to_process]
    if on_progress:
        on_progress({"total": total, "processed": processed, "current": "批量解析中（MinerU）", **results})

    try:
        extract_results = loader.extract_batch(pdf_paths)
    except Exception as exc:
        for entity_id in to_process:
            parse_store.mark(
                parse_conn, entity_id, "failed",
                source_pdf_path=str(download_store.paper_path(entity_id)), error=str(exc)[:500],
            )
        results["failed"] += len(to_process)
        processed += len(to_process)
        if on_progress:
            on_progress({"total": total, "processed": processed, "current": "", **results})
        return {"total": total, **results}

    if len(extract_results) != len(to_process):
        # Guard against silent cross-entity attribution: if MinerU returns fewer results than PDFs
        # (e.g. one doc dropped without raising), pairing by zip would mislabel papers.
        msg = f"MinerU 返回 {len(extract_results)} 份结果，期望 {len(to_process)} 份；已跳过本轮解析"
        for entity_id in to_process:
            parse_store.mark(
                parse_conn, entity_id, "failed",
                source_pdf_path=str(download_store.paper_path(entity_id)), error=msg[:500],
            )
        return {
            "total": total, "parsed": 0, "failed": len(to_process),
            "skipped": results["skipped"], "no_pdf": results["no_pdf"],
        }

    for entity_id, result in zip(to_process, extract_results):
        if is_cancelled and is_cancelled():
            break
        pdf_path = download_store.paper_path(entity_id)
        parse_store.mark(parse_conn, entity_id, "parsing", source_pdf_path=str(pdf_path))
        try:
            if result.markdown is None:
                raise ValueError(f"MinerU 解析失败: {result.error or result.state or '未知错误'}")
            dest = parse_store.parsed_path(entity_id)
            result.save_markdown(str(dest), with_images=True)
            parse_store.mark(
                parse_conn, entity_id, "success",
                source_pdf_path=str(pdf_path), markdown_path=str(dest), char_count=len(result.markdown),
            )
            results["parsed"] += 1
        except Exception as exc:
            parse_store.mark(
                parse_conn, entity_id, "failed",
                source_pdf_path=str(pdf_path), error=str(exc)[:500],
            )
            results["failed"] += 1
        processed += 1
        if on_progress:
            on_progress({"total": total, "processed": processed, "current": entity_id, **results})

    return {"total": total, **results}
