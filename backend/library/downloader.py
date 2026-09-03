from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from backend.library import planner, store


USER_AGENT = "AIPaperbaseAgent-download/0.1 (personal research; contact: local-user)"
PDF_HEADER = b"%PDF"
MIN_PDF_BYTES = 1024


def download_pdf(
    url: str, *, timeout: int = 90, retries: int = 2, delay: float = 0.2
) -> tuple[bytes, int]:
    """Download a PDF and return (content, size). Raises on repeated failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
            if not content.startswith(PDF_HEADER):
                raise ValueError("响应不是有效 PDF（缺少 %PDF 头）")
            if len(content) < MIN_PDF_BYTES:
                raise ValueError("PDF 文件过小，可能不是完整论文")
            time.sleep(delay)
            return content, len(content)
        except Exception as exc:  # Network errors and validation failures are both retried; finally raise
            last_error = exc
            time.sleep(min(delay * (attempt + 1), 2.0))
    raise RuntimeError(f"下载失败: {last_error}")


def _atomic_write(dest: Path, content: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def run_download(
    catalog_conn: sqlite3.Connection,
    store_conn: sqlite3.Connection,
    entity_ids: list[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    workers: int = 4,
) -> dict[str, int]:
    """Concurrently download all downloadable entities, recording status and deduplicating by content."""
    plan = planner.build_plan(catalog_conn, entity_ids)
    downloadable = [item for item in plan["items"] if item["downloadable"]]
    total = len(downloadable)
    results = {"success": 0, "failed": 0, "duplicate": 0, "skipped": 0}

    to_process = []
    for item in downloadable:
        if store.is_success(store_conn, item["entity_id"]):
            results["skipped"] += 1
        else:
            to_process.append(item)
    processed = results["skipped"]
    if on_progress:
        on_progress({"total": total, "processed": processed, "current": "", **results})
    if not to_process:
        return {"total": total, **results}

    lock = threading.Lock()  # sqlite connections are not thread-safe; serialize store operations with a lock

    def process(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        entity_id = item["entity_id"]
        with lock:
            store.mark(store_conn, entity_id, "downloading", source=item["source"], download_url=item["download_url"])
        try:
            content, size = download_pdf(item["download_url"])
            digest = hashlib.sha256(content).hexdigest()
            # Dedup check + file write + mark are all kept under one lock, so the same content is written only once under concurrency
            with lock:
                existing = store.find_by_sha256(store_conn, digest)
                if existing and existing != entity_id:
                    source_path = store.paper_path(existing)
                    if source_path.exists():
                        dest = store.paper_path(entity_id)
                        # Give this entity its own PDF too (hard link, near-zero disk) so every
                        # downstream step that keys on file existence (is_success / parse / ingest)
                        # keeps working. It stays labelled 'duplicate' so the UI shows shared content.
                        if not dest.exists():
                            try:
                                os.link(source_path, dest)
                            except OSError:
                                shutil.copyfile(source_path, dest)
                        store.mark(
                            store_conn, entity_id, "duplicate",
                            source=item["source"], download_url=item["download_url"],
                            sha256=digest, file_path=str(dest),
                            bytes=size, duplicate_of=existing,
                        )
                        return "duplicate", item
                    # sha record exists but its file is gone: not a usable duplicate, download normally.
                dest = store.paper_path(entity_id)
                _atomic_write(dest, content)
                store.mark(
                    store_conn, entity_id, "success",
                    source=item["source"], download_url=item["download_url"],
                    sha256=digest, file_path=str(dest), bytes=size,
                )
                return "success", item
        except Exception as exc:  # noqa: BLE001
            with lock:
                store.mark(
                    store_conn, entity_id, "failed",
                    source=item["source"], download_url=item["download_url"],
                    error=str(exc)[:500],
                )
            return "failed", item

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = []
        for item in to_process:
            if is_cancelled and is_cancelled():
                break
            futures.append(executor.submit(process, item))
        for future in as_completed(futures):
            status, item = future.result()
            results[status] += 1
            processed += 1
            if on_progress:
                on_progress({"total": total, "processed": processed, "current": item["title"], **results})

    return {"total": total, **results}
