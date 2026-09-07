from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend.catalog import queries
from backend.analytics import queries as analytics_queries
from backend.catalog.database import DEFAULT_DATABASE, ROOT, connect
from backend.chats import database as chats_db
from backend.chats import service as chats_service
from backend.chats import store as chats_store
from backend.collections import database as collections_db
from backend.collections import service as collections_service
from backend.collections import export as collections_export
from backend.collections import organization as collections_organization
from backend.library import planner as library_planner
from backend.library import tasks as download_tasks
from backend.library import store as download_store
from backend.library import downloader as library_downloader
from backend.library import ingest as library_ingest
from backend.library import sources as library_sources
from backend.library import local_status as library_local_status
from backend.rag import parser as rag_parser
from backend.rag import parse_store as rag_store
from backend.rag import service as rag_service


FRONTEND_ROOT = ROOT / "frontend"


class Handler(BaseHTTPRequestHandler):
    database_path = DEFAULT_DATABASE
    collections_path = collections_db.DEFAULT_DATABASE
    chats_path = chats_db.DEFAULT_DATABASE

    def log_message(self, format: str, *args: object) -> None:
        print(f"[api] {self.address_string()} - {format % args}")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def send_file(self, body: bytes, content_type: str, filename: str | None = None, *, disposition: str = "attachment") -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        if filename:
            safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-._") else "_" for ch in filename)
            self.send_header("Content-Disposition", f'{disposition}; filename="{safe}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"请求体不是合法 JSON: {exc}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _organization_request(self, callback):
        try:
            callback()
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"草稿或集合不存在: {exc}")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "操作失败，未完成写入，请重试")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api(parsed.path, parse_qs(parsed.query))
        else:
            self.handle_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/research"):
            from backend.research.api import handle
            self._organization_request(lambda: handle(self, parsed.path, write=True))
            return
        if parsed.path == "/api/downloads":
            self._handle_start_download()
            return
        if parsed.path == "/api/parse":
            self._handle_start_parse()
            return
        if parsed.path == "/api/rag/ingest":
            self._handle_rag_ingest()
            return
        if parsed.path == "/api/rag/query":
            self._handle_rag_query()
            return
        if parsed.path == "/api/chats":
            self._handle_create_chat()
            return
        if parsed.path == "/api/chats/ask/cancel":
            self._handle_chat_ask_cancel()
            return
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "chats":
            if parts[3] == "ask":
                self._handle_chat_ask(parts[2])
                return
            if parts[3] == "regenerate":
                self._handle_chat_regenerate(parts[2])
                return
            if parts[3] == "rewrite":
                self._handle_chat_rewrite(parts[2])
                return
            if parts[3] == "snapshots":
                self._handle_chat_snapshots(parts[2])
                return
            if parts[3] == "ingest":
                self._handle_chat_ingest(parts[2])
                return
        if len(parts) == 6 and parts[0] == "api" and parts[1] == "chats" and parts[3] == "snapshots":
            if parts[5] == "restore":
                self._handle_chat_snapshot_restore(parts[2], parts[4])
                return
        if parsed.path == "/api/cleanup":
            self._handle_cleanup()
            return
        if parsed.path == "/api/organization/plan":
            self._organization_request(self._handle_organization_plan)
            return
        if parsed.path == "/api/organization/apply":
            self._organization_request(self._handle_organization_apply)
            return
        self._handle_collection_method("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "chats":
            self._handle_patch_chat(parts[2])
            return
        self._handle_collection_method("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "chats":
            self._handle_delete_chat(parts[2])
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "downloads":
            self._handle_delete_download(parts[2])
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "parse":
            self._handle_delete_parse(parts[2])
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "rag":
            self._handle_delete_ingest(parts[2])
            return
        self._handle_collection_method("DELETE")

    def _handle_collection_method(self, method: str) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/collections"):
            self.send_error_json(HTTPStatus.NOT_FOUND, "API 不存在")
            return
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self.handle_collections(method, parsed.path, parse_qs(parsed.query), body)

    def handle_api(self, path: str, params: dict[str, list[str]]) -> None:
        if path.startswith("/api/research"):
            from backend.research.api import handle
            self._organization_request(lambda: handle(self, path, params))
            return
        if path == "/api/downloads/status":
            self._handle_task_status(params)
            return
        if path == "/api/parse/status":
            self._handle_task_status(params)
            return
        if path == "/api/rag/status":
            self._handle_rag_status()
            return
        if path == "/api/rag/ingest/status":
            self._handle_task_status(params)
            return
        if path == "/api/chats/ask/status":
            self._handle_task_status(params)
            return
        if path == "/api/chats":
            self._handle_list_chats(params)
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "chats" and parts[3] == "ingest-tasks":
            self._handle_chat_ingest_tasks(parts[2])
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "chats" and parts[3] == "messages":
            self._handle_chat_messages(parts[2])
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "chats" and parts[3] == "snapshots":
            self._handle_chat_snapshots(parts[2])
            return
        if len(parts) == 5 and parts[0] == "api" and parts[1] == "chats" and parts[3] == "snapshots":
            self._handle_chat_snapshot_detail(parts[2], parts[4])
            return
        if path == "/api/downloads":
            self._handle_downloads_list()
            return
        if path == "/api/my-library":
            self._handle_local_library(include_saved=True)
            return
        if path == "/api/local-library":
            self._handle_local_library()
            return
        if path.startswith("/api/organization-runs/"):
            self._organization_request(lambda: self._handle_organization_run(path.rsplit("/", 1)[-1]))
            return
        if path.startswith("/api/downloads/") and path.endswith("/pdf"):
            self._handle_download_pdf(path.split("/")[-2])
            return
        if path.startswith("/api/parse/") and path.endswith("/md"):
            self._handle_view_markdown(path.split("/")[-2])
            return
        if path.startswith("/api/collections"):
            self.handle_collections("GET", path, params, None)
            return
        if path == "/api/download-plan":
            self._handle_download_plan(params)
            return
        if path == "/api/ingest-plan":
            self._handle_ingest_plan(params)
            return
        if not self.database_path.exists():
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "数据库尚未构建，请先运行导入命令")
            return
        try:
            with connect(self.database_path, read_only=True) as connection:
                if path == "/api/health":
                    payload = {"status": "ok", "database": str(self.database_path)}
                elif path == "/api/summary":
                    payload = queries.summary(connection)
                elif path == "/api/venues":
                    payload = {"items": queries.venues(connection)}
                elif path == "/api/years":
                    payload = {"items": queries.years(connection)}
                elif path == "/api/quality":
                    payload = queries.quality(connection)
                elif path == "/api/entity-quality":
                    payload = queries.entity_quality(connection)
                elif path == "/api/entities":
                    payload = queries.entities(
                        connection,
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                        entity_status=self.param(params, "entity_status", "merged"),
                        search=self.param(params, "search"),
                    )
                elif path == "/api/entity-candidates":
                    payload = queries.entity_candidates(
                        connection,
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                        review_status=self.param(params, "review_status", "pending"),
                    )
                elif path == "/api/topics":
                    payload = analytics_queries.topic_overview(connection)
                elif path == "/api/venue-overview":
                    payload = analytics_queries.venue_overview(connection)
                elif path == "/api/topic-detail":
                    payload = analytics_queries.topic_detail(
                        connection, topic_id=self.param(params, "topic_id"),
                    )
                elif path == "/api/venue-detail":
                    payload = analytics_queries.venue_detail(
                        connection, venue=self.param(params, "venue"),
                    )
                elif path == "/api/venue-papers":
                    payload = analytics_queries.venue_papers(
                        connection,
                        venue=self.param(params, "venue"),
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                    )
                elif path == "/api/topic-trends":
                    payload = analytics_queries.topic_trends(connection)
                elif path == "/api/topic-papers":
                    year_value = self.param(params, "year")
                    payload = analytics_queries.topic_papers(
                        connection,
                        topic_id=self.param(params, "topic_id"),
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                        year=int(year_value) if year_value else None,
                        venue=self.param(params, "venue"),
                        search=self.param(params, "search"),
                    )
                elif path == "/api/topic-papers/entity-ids":
                    year_value = self.param(params, "year")
                    payload = analytics_queries.topic_papers_entity_ids(
                        connection,
                        topic_id=self.param(params, "topic_id"),
                        year=int(year_value) if year_value else None,
                        venue=self.param(params, "venue"),
                        search=self.param(params, "search"),
                    )
                elif path == "/api/topic-evaluation":
                    payload = analytics_queries.topic_evaluation(connection)
                elif path == "/api/topic-evaluation-samples":
                    payload = analytics_queries.topic_evaluation_samples(
                        connection,
                        topic_id=self.param(params, "topic_id"),
                        verdict=self.param(params, "verdict"),
                        score_band=self.param(params, "score_band"),
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                    )
                elif path == "/api/papers":
                    year_value = self.param(params, "year")
                    payload = queries.papers(
                        connection,
                        page=self.int_param(params, "page", 1),
                        page_size=self.int_param(params, "page_size", 25),
                        venue=self.param(params, "venue"),
                        year=int(year_value) if year_value else None,
                        venue_type=self.param(params, "venue_type"),
                        list_status=self.param(params, "list_status"),
                        has_abstract=self.param(params, "has_abstract"),
                        search=self.param(params, "search"),
                    )
                elif path == "/api/papers/entity-ids":
                    year_value = self.param(params, "year")
                    payload = queries.papers_entity_ids(
                        connection,
                        venue=self.param(params, "venue"),
                        year=int(year_value) if year_value else None,
                        venue_type=self.param(params, "venue_type"),
                        list_status=self.param(params, "list_status"),
                        has_abstract=self.param(params, "has_abstract"),
                        search=self.param(params, "search"),
                    )
                else:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "API 不存在")
                    return
            self.send_json(payload)
        except (ValueError, TypeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # keep the local dashboard useful while exposing failures
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {exc}")

    @staticmethod
    def param(params: dict[str, list[str]], key: str, default: str = "") -> str:
        return params.get(key, [default])[0].strip()

    @classmethod
    def int_param(cls, params: dict[str, list[str]], key: str, default: int) -> int:
        value = cls.param(params, key)
        return int(value) if value else default

    def handle_collections(
        self, method: str, path: str, params: dict[str, list[str]], body: dict | None
    ) -> None:
        if not self.database_path.exists():
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "目录数据库尚未构建，请先运行导入命令")
            return
        try:
            parts = [part for part in path.split("/") if part]
            if len(parts) < 2 or parts[0] != "api" or parts[1] != "collections":
                self.send_error_json(HTTPStatus.NOT_FOUND, "API 不存在")
                return
            rest = parts[2:]
            if not rest:
                if method == "GET":
                    self._handle_list_collections()
                elif method == "POST":
                    self._handle_create_collection(body or {})
                else:
                    self.send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "方法不支持")
                return
            collection_id = rest[0]
            if len(rest) == 1:
                if method == "GET":
                    self._handle_get_collection(collection_id)
                elif method == "PATCH":
                    self._handle_update_collection(collection_id, body or {})
                elif method == "DELETE":
                    self._handle_delete_collection(collection_id)
                else:
                    self.send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "方法不支持")
                return
            if len(rest) == 2 and rest[1] == "members":
                if method == "POST":
                    self._handle_add_members(collection_id, body or {})
                elif method == "DELETE":
                    self._handle_remove_members(collection_id, body or {})
                else:
                    self.send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "方法不支持")
                return
            if len(rest) == 2 and rest[1] == "export":
                if method == "GET":
                    self._handle_export_collection(collection_id, self.param(params, "format", "csv"))
                else:
                    self.send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "方法不支持")
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "API 不存在")
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"集合不存在: {exc}")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {exc}")

    def _open_user_connection(self, read_only: bool = False):
        if read_only and not self.collections_path.exists():
            import sqlite3
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            collections_db.initialize(connection)
            connection.execute("PRAGMA query_only=ON")
            return connection
        connection = collections_db.connect(self.collections_path, read_only=read_only)
        if not read_only:
            collections_db.initialize(connection)
        return connection

    def _open_chats_connection(self, read_only: bool = False):
        connection = chats_db.connect(self.chats_path, read_only=read_only)
        if not read_only:
            chats_db.initialize(connection)
        return connection

    def _handle_list_chats(self, params: dict[str, list[str]]) -> None:
        status = self.param(params, "status")
        if status not in (None, "", "active", "archived"):
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"未知会话状态: {status}")
            return
        if not self.chats_path.exists():
            self.send_json({"items": [], "total": 0})
            return
        conn = self._open_chats_connection(read_only=True)
        try:
            items = chats_store.list_conversations(conn, status or None)
        finally:
            conn.close()
        self.send_json({"items": items, "total": len(items)})

    def _handle_create_chat(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        conn = self._open_chats_connection()
        try:
            conversation = chats_store.create_conversation(conn, title=(body or {}).get("title", ""))
        finally:
            conn.close()
        self.send_json(conversation, HTTPStatus.CREATED)

    def _handle_patch_chat(self, conversation_id: str) -> None:
        try:
            body = self.read_json_body()
            conn = self._open_chats_connection()
            try:
                conversation = chats_store.update_conversation(
                    conn, conversation_id,
                    title=(body or {}).get("title"),
                    status=(body or {}).get("status"),
                )
            finally:
                conn.close()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"会话不存在: {conversation_id}")
            return
        self.send_json(conversation)

    def _handle_delete_chat(self, conversation_id: str) -> None:
        conn = self._open_chats_connection()
        try:
            chats_store.delete_conversation(conn, conversation_id)
        except KeyError:
            conn.close()
            self.send_error_json(HTTPStatus.NOT_FOUND, f"会话不存在: {conversation_id}")
            return
        finally:
            conn.close()
        self.send_json({"deleted": conversation_id})

    def _handle_chat_messages(self, conversation_id: str) -> None:
        if not self.chats_path.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, f"会话不存在: {conversation_id}")
            return
        conn = self._open_chats_connection(read_only=True)
        try:
            conversation = chats_store.get_conversation(conn, conversation_id)
            if conversation is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, f"会话不存在: {conversation_id}")
                return
            messages = chats_store.list_messages(conn, conversation_id)
        finally:
            conn.close()
        self.send_json({"conversation": conversation, "messages": messages})

    def _handle_chat_ask(self, conversation_id: str) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        query = ((body or {}).get("query") or "").strip()
        # top_k defaults to .env RAG_TOP_K (frontend no longer sends it; body kept for debugging)
        raw_top_k = (body or {}).get("top_k")
        if raw_top_k is None or raw_top_k == "":
            top_k = int(os.environ.get("RAG_TOP_K", "5") or "5")
        else:
            try:
                top_k = int(raw_top_k)
            except (TypeError, ValueError):
                self.send_error_json(HTTPStatus.BAD_REQUEST, "top_k 必须是整数")
                return
        mode = (body or {}).get("mode") or "agent"
        if not query:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "问题不能为空")
            return
        if top_k not in (3, 5, 10):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "top_k 仅支持 3/5/10")
            return
        if mode not in ("pipeline", "agent"):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "mode 仅支持 pipeline/agent")
            return

        from backend.research import store as research_store
        research_conn = self._open_user_connection(read_only=True)
        try:
            scope = research_store.chat_scope(research_conn, conversation_id)
        finally:
            research_conn.close()
        if scope is not None and mode != "agent":
            self.send_error_json(HTTPStatus.BAD_REQUEST, "限定范围会话请使用 Agent 模式")
            return

        def job(update, is_cancelled):
            conn = self._open_chats_connection()
            try:
                message = chats_service.ask(
                    conn, conversation_id=conversation_id, query=query, top_k=top_k, mode=mode,
                    is_cancelled=is_cancelled, scope=scope,
                )
            finally:
                conn.close()
            return {"message": message}

        try:
            task_id = download_tasks.start_job(
                job,
                max_active=int(os.environ.get("ASK_MAX_CONCURRENCY", "2")),
                timeout=float(os.environ.get("ASK_TIMEOUT", "600")),
            )
        except RuntimeError as exc:
            self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, str(exc))
            return
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_chat_ask_cancel(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        task_id = ((body or {}).get("task_id") or "").strip()
        if not task_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 task_id")
            return
        download_tasks.cancel(task_id)
        self.send_json({"cancelled": task_id})

    def _handle_chat_rewrite(self, conversation_id: str) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        message_id = ((body or {}).get("message_id") or "").strip()
        content = ((body or {}).get("content") or "").strip()
        if not message_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 message_id")
            return
        if not content:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "修改后内容不能为空")
            return
        conn = self._open_chats_connection()
        try:
            result = chats_service.rewrite(
                conn, conversation_id=conversation_id, message_id=message_id, content=content
            )
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"消息不存在: {exc}")
            return
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        finally:
            conn.close()
        self.send_json(result)

    def _handle_chat_snapshots(self, conversation_id: str) -> None:
        conn = self._open_chats_connection(read_only=True)
        try:
            items = chats_service.list_snapshots(conn, conversation_id=conversation_id)
        finally:
            conn.close()
        self.send_json({"items": items, "total": len(items)})

    def _handle_chat_snapshot_detail(self, conversation_id: str, snapshot_id: str) -> None:
        conn = self._open_chats_connection(read_only=True)
        try:
            snapshot = chats_service.get_snapshot(conn, conversation_id=conversation_id, snapshot_id=snapshot_id)
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"快照不存在: {exc}")
            return
        finally:
            conn.close()
        self.send_json(snapshot)

    def _handle_chat_ingest_tasks(self, conversation_id: str) -> None:
        conn = self._open_chats_connection(read_only=True)
        try:
            self.send_json({"items": chats_store.list_ingest_tasks(conn, conversation_id)})
        finally:
            conn.close()

    def _handle_chat_snapshot_restore(self, conversation_id: str, snapshot_id: str) -> None:
        conn = self._open_chats_connection()
        try:
            copy = chats_service.restore_snapshot(conn, conversation_id=conversation_id, snapshot_id=snapshot_id)
        except KeyError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"快照不存在: {exc}")
            return
        finally:
            conn.close()
        self.send_json(copy, HTTPStatus.CREATED)

    def _handle_chat_ingest(self, conversation_id: str) -> None:
        """Confirmation-execution endpoint for the execution-class tool: a three-stage pipeline of
        download→parse→ingest (idempotent, only performs missing steps), then appends a system message to the conversation."""
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entity_ids = self._body_entity_ids(body)
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids")
            return

        # Confirmation returns immediately.  This is intentionally local-only;
        # potentially slow arXiv/OpenAlex resolution happens inside the job.
        catalog_ro = connect(self.database_path, read_only=True)
        try:
            plan = library_planner.build_plan(catalog_ro, entity_ids)
            self._enrich_download_status(plan)
        finally:
            catalog_ro.close()
        plan_items = plan["items"]

        def job(update, is_cancelled):
            catalog_conn = connect(self.database_path, read_only=True)
            store_conn = download_store.connect()
            download_store.initialize(store_conn)
            parse_conn = rag_store.connect()
            rag_store.initialize(parse_conn)
            try:
                update({"stage": "resolving_external", "current": "正在查找可信开放来源…"})
                # Rebuild only after confirmation, now permitting read-only
                # source lookups. A failure remains an item-level unavailable
                # result rather than blocking the confirmation request itself.
                resolve_ids = [item["requested_entity_id"] for item in plan_items
                               if item.get("ingest_status") != "success"
                               and item.get("download_status") not in {"success", "duplicate"}]
                resolved_plan = library_planner.build_plan(
                    catalog_conn, resolve_ids, arxiv_lookup=library_sources.remote_arxiv_candidates,
                    openalex_lookup=library_sources.remote_openalex_candidates,
                )
                resolved = {item["requested_entity_id"]: item for item in resolved_plan["items"]}
                execution_items = [resolved.get(item["requested_entity_id"], item) for item in plan_items]
                execution_canonical = {item["requested_entity_id"]: item["canonical_entity_id"] for item in execution_items}
                execution_runnable = []
                execution_seen: set[str] = set()
                for item in execution_items:
                    canonical = item["canonical_entity_id"]
                    needs_work = item.get("ingest_status") != "success" and (
                        item.get("downloadable") or item.get("download_status") in {"success", "duplicate"}
                    )
                    if needs_work and canonical not in execution_seen:
                        execution_runnable.append(item)
                        execution_seen.add(canonical)
                download = {"success": 0, "failed": 0}
                parsed = {"parsed": 0, "failed": 0}
                indexed: dict = {"documents": 0, "ingested_chunks": 0, "already_indexed": 0}
                if not is_cancelled():
                    download = library_downloader.run_download(
                        catalog_conn, store_conn, [item["canonical_entity_id"] for item in execution_runnable],
                        is_cancelled=is_cancelled, on_progress=update, plan_items=execution_runnable,
                    )
                # A duplicate PDF belongs to another entity. Keep its item visible
                # but do not create a second parsed/vector representation or merge
                # catalog identities implicitly.
                parse_ids = library_ingest.parseable_ids_after_pdf_dedup(store_conn, execution_runnable)
                if not is_cancelled():
                    parsed = rag_parser.run_parse(
                        store_conn, parse_conn, parse_ids,
                        is_cancelled=is_cancelled, on_progress=update,
                    )
                if not is_cancelled():
                    indexed = rag_service.ingest_parsed(
                        catalog_conn, parse_conn, parse_ids,
                        on_progress=update, is_cancelled=is_cancelled, canonical_ids=execution_canonical,
                    )
                result = library_ingest.finalize(execution_items, download or {}, parsed or {}, indexed or {})
                # append a system message on completion
                try:
                    chats_conn = self._open_chats_connection()
                    try:
                        chats_store = __import__("backend.chats.store", fromlist=["add_message"]).add_message
                        chats_store(
                            chats_conn, conversation_id, "assistant",
                            content=library_ingest.completion_message(result),
                        )
                    finally:
                        chats_conn.close()
                except Exception as exc:
                    result["message_error"] = str(exc)[:500]
                return result
            finally:
                parse_conn.close()
                store_conn.close()
                catalog_conn.close()

        def started(task_id: str) -> None:
            conn = self._open_chats_connection()
            try:
                chats_store.create_ingest_task(conn, task_id, conversation_id, entity_ids)
            finally:
                conn.close()

        def finished(state: dict) -> None:
            conn = self._open_chats_connection()
            try:
                previous = chats_store.get_ingest_task(conn, state["task_id"])
                chats_store.finish_ingest_task(
                    conn, state["task_id"], status=state.get("status", "error"),
                    result={k: v for k, v in state.items() if k not in {"error", "cancel"}},
                    error=state.get("error", ""),
                )
                if state.get("status") == "error" and previous and previous.get("status") != "error":
                    chats_store.add_message(
                        conn, conversation_id, "assistant",
                        content=f"入库任务失败：{state.get('error') or '未知错误'}",
                        error=(state.get("error") or "")[:500],
                    )
            finally:
                conn.close()

        try:
            task_id = download_tasks.start_job(
                job,
                max_active=int(os.environ.get("ASK_MAX_CONCURRENCY", "2")),
                timeout=float(os.environ.get("ASK_TIMEOUT", "600")),
                on_started=started, on_finished=finished,
            )
        except RuntimeError as exc:
            self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, str(exc))
            return
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_chat_regenerate(self, conversation_id: str) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        message_id = ((body or {}).get("message_id") or "").strip()
        if not message_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 message_id")
            return

        def job(update, is_cancelled):
            conn = self._open_chats_connection()
            try:
                message = chats_service.regenerate(
                    conn, conversation_id=conversation_id, message_id=message_id
                )
            finally:
                conn.close()
            return {"message": message}

        task_id = download_tasks.start_job(job)
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_list_collections(self) -> None:
        if not self.collections_path.exists():
            self.send_json({"items": [], "total": 0})
            return
        user_conn = self._open_user_connection(read_only=True)
        try:
            items = collections_service.list_collections(user_conn)
        finally:
            user_conn.close()
        self.send_json({"items": items, "total": len(items)})

    def _handle_get_collection(self, collection_id: str) -> None:
        if not self.collections_path.exists():
            raise KeyError(collection_id)
        user_conn = self._open_user_connection(read_only=True)
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            payload = collections_service.get_collection(user_conn, catalog_conn, collection_id)
        finally:
            user_conn.close()
            catalog_conn.close()
        self.send_json(payload)

    @staticmethod
    def _snapshot(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return ""

    @staticmethod
    def _body_entity_ids(body: dict) -> list[str]:
        ids = body.get("entity_ids") or []
        if not isinstance(ids, list):
            return []
        return [str(item).strip() for item in ids if str(item).strip()]

    def _handle_create_collection(self, body: dict) -> None:
        user_conn = self._open_user_connection()
        collections_service.ensure_schema(user_conn)
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            release_id = collections_service.current_catalog_release(catalog_conn)
            result = collections_service.create_collection(
                user_conn,
                name=body.get("name", ""),
                description=body.get("description", ""),
                source_type=body.get("source_type", "manual"),
                source_snapshot=self._snapshot(body.get("source_snapshot", "")),
                catalog_release_id=release_id,
                entity_ids=body.get("entity_ids") or [],
                added_by=body.get("added_by", "manual"),
            )
        finally:
            user_conn.close()
            catalog_conn.close()
        self.send_json(result, HTTPStatus.CREATED)

    def _handle_update_collection(self, collection_id: str, body: dict) -> None:
        user_conn = self._open_user_connection()
        collections_service.ensure_schema(user_conn)
        try:
            result = collections_service.update_collection(
                user_conn,
                collection_id,
                name=body.get("name"),
                description=body.get("description"),
            )
        finally:
            user_conn.close()
        self.send_json(result)

    def _handle_delete_collection(self, collection_id: str) -> None:
        user_conn = self._open_user_connection()
        collections_service.ensure_schema(user_conn)
        try:
            collections_service.delete_collection(user_conn, collection_id)
        finally:
            user_conn.close()
        self.send_json({"deleted": collection_id})

    def _handle_add_members(self, collection_id: str, body: dict) -> None:
        entity_ids = body.get("entity_ids") or []
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "未提供 entity_ids")
            return
        user_conn = self._open_user_connection()
        collections_service.ensure_schema(user_conn)
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            result = collections_service.add_members(
                user_conn, catalog_conn, collection_id,
                entity_ids, added_by=body.get("added_by", "manual"),
            )
        finally:
            user_conn.close()
            catalog_conn.close()
        self.send_json(result)

    def _handle_remove_members(self, collection_id: str, body: dict) -> None:
        entity_ids = body.get("entity_ids") or []
        user_conn = self._open_user_connection()
        collections_service.ensure_schema(user_conn)
        try:
            result = collections_service.remove_members(user_conn, collection_id, entity_ids)
        finally:
            user_conn.close()
        self.send_json(result)

    def _handle_export_collection(self, collection_id: str, fmt: str) -> None:
        user_conn = self._open_user_connection(read_only=True)
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            content, content_type, filename = collections_export.export_collection(
                user_conn, catalog_conn, collection_id, fmt
            )
        finally:
            user_conn.close()
            catalog_conn.close()
        self.send_file(content, content_type, filename)

    def _handle_download_plan(self, params: dict[str, list[str]]) -> None:
        collection_id = self.param(params, "collection_id")
        if not collection_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 collection_id")
            return
        if not self.database_path.exists():
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "目录数据库尚未构建，请先运行导入命令")
            return
        try:
            if not self.collections_path.exists():
                raise KeyError(collection_id)
            user_conn = self._open_user_connection(read_only=True)
            catalog_conn = connect(self.database_path, read_only=True)
            try:
                entity_ids = collections_service.collection_entity_ids(user_conn, collection_id)
                payload = library_planner.build_plan(catalog_conn, entity_ids)
                self._enrich_download_status(payload)
            finally:
                user_conn.close()
                catalog_conn.close()
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, f"集合不存在: {collection_id}")
            return
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {exc}")
            return
        self.send_json(payload)

    def _handle_ingest_plan(self, params: dict[str, list[str]]) -> None:
        raw = self.param(params, "entity_ids")
        entity_ids = [value for value in raw.split(",") if value]
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids")
            return
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            payload = library_planner.build_plan(catalog_conn, entity_ids)
            self._enrich_download_status(payload)
            self.send_json(payload)
        finally:
            catalog_conn.close()

    def _handle_start_download(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entity_ids = self._body_entity_ids(body)
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids（请勾选论文）")
            return
        if not self.database_path.exists():
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "目录数据库尚未构建，请先运行导入命令")
            return

        def job(update, is_cancelled):
            catalog_conn = connect(self.database_path, read_only=True)
            store_conn = download_store.connect()
            download_store.initialize(store_conn)
            try:
                return library_downloader.run_download(
                    catalog_conn, store_conn, entity_ids,
                    is_cancelled=is_cancelled, on_progress=update,
                )
            finally:
                store_conn.close()
                catalog_conn.close()

        task_id = download_tasks.start_job(job)
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_start_parse(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entity_ids = self._body_entity_ids(body)
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids（请勾选论文）")
            return
        if not download_store.DEFAULT_DATABASE.exists():
            self.send_error_json(HTTPStatus.BAD_REQUEST, "还没有下载任何论文，请先下载")
            return

        def job(update, is_cancelled):
            download_conn = download_store.connect(read_only=True)
            parse_conn = rag_store.connect()
            rag_store.initialize(parse_conn)
            try:
                return rag_parser.run_parse(
                    download_conn, parse_conn, entity_ids,
                    is_cancelled=is_cancelled, on_progress=update,
                )
            finally:
                parse_conn.close()
                download_conn.close()

        task_id = download_tasks.start_job(job)
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_task_status(self, params: dict[str, list[str]]) -> None:
        task_id = self.param(params, "task_id")
        if not task_id:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 task_id")
            return
        state = download_tasks.get(task_id)
        if state is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "任务不存在")
            return
        self.send_json(state)

    def _handle_downloads_list(self) -> None:
        if not download_store.DEFAULT_DATABASE.exists():
            self.send_json({"items": [], "counts": {}})
            return
        conn = download_store.connect(read_only=True)
        try:
            items = download_store.list_all(conn)
            counts = download_store.status_counts(conn)
        finally:
            conn.close()
        self.send_json({"items": items, "counts": counts})

    def _handle_local_library(self, include_saved=False) -> None:
        """Read-only aggregation; downloads/parsed manifests remain the source of truth."""
        catalog_conn = connect(self.database_path, read_only=True)
        user_conn = self._open_user_connection(read_only=True)
        try:
            payload = self._local_reconcile(catalog_conn)
            memberships = user_conn.execute("SELECT m.entity_id,c.collection_id,c.name FROM collection_members m JOIN collections c USING(collection_id)").fetchall()
            identities = collections_service.resolve_entity_ids(catalog_conn, [r["entity_id"] for r in memberships])
            if include_saved:
                present = {item["entity_id"] for item in payload["items"]}
                saved_ids = list(dict.fromkeys(info["entity_id"] for info in identities.values() if info["status"] != "missing" and info["entity_id"] not in present))
                metadata = collections_organization._metadata(catalog_conn, saved_ids)
                payload["items"].extend({"entity_id": eid, "title": metadata.get(eid, {}).get("title", eid)} for eid in saved_ids)
            for item in payload["items"]:
                item["collections"] = [{"collection_id": r["collection_id"], "name": r["name"]} for r in memberships
                    if identities[r["entity_id"]]["entity_id"] == item["entity_id"]]
            payload["catalog_total"] = catalog_conn.execute("SELECT COUNT(*) FROM paper_entities").fetchone()[0]
            self.send_json(payload)
        finally:
            user_conn.close()
            catalog_conn.close()

    def _local_reconcile(self, catalog_conn, *, probe_vectors=True):
        downloads_conn = download_store.connect(read_only=True) if download_store.DEFAULT_DATABASE.exists() else None
        parsed_conn = rag_store.connect(read_only=True) if rag_store.DEFAULT_DATABASE.exists() else None
        try:
            counts, fingerprint = rag_service.index_reconcile_snapshot() if probe_vectors else (None, "")
            return library_local_status.snapshot(catalog_conn, downloads_conn=downloads_conn, parsed_conn=parsed_conn,
                                                 document_chunk_counts=counts, pipeline_fingerprint=fingerprint)
        finally:
            if downloads_conn is not None:
                downloads_conn.close()
            if parsed_conn is not None:
                parsed_conn.close()

    def _handle_organization_plan(self) -> None:
        body = self.read_json_body()
        collection_ids = body.get("collection_ids") or []
        user_conn = self._open_user_connection()
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            collections_service.ensure_schema(user_conn)
            reconcile = self._local_reconcile(catalog_conn, probe_vectors=body.get("operation") != "assign")
            # Explicit selections may be catalog metadata without a local PDF.
            # Never extend the scope of an all-local request to the catalog.
            if body.get("operation") == "assign" and isinstance(body.get("entity_ids"), list):
                requested = collections_service.resolve_entity_ids(catalog_conn, body["entity_ids"])
                known = {item["entity_id"] for item in reconcile["items"]}
                metadata = collections_organization._metadata(catalog_conn, list(dict.fromkeys(info["entity_id"] for info in requested.values() if info["status"] != "missing")))
                reconcile["items"].extend({"entity_id": eid, "title": item["title"]} for eid, item in metadata.items() if eid not in known)
            result = collections_organization.propose(
                user_conn, catalog_conn, reconcile,
                operation=str(body.get("operation") or "classify"), target_name=str(body.get("target_name") or ""),
                collection_ids=[str(x) for x in collection_ids], conversation_id=str(body.get("conversation_id") or ""),
                entity_ids=[str(x) for x in body.get("entity_ids", [])] if isinstance(body.get("entity_ids"), list) else None,
                allow_all=bool(body.get("allow_all", False)),
                rules=body.get("rules") if isinstance(body.get("rules"), dict) else None,
            )
        finally:
            user_conn.close(); catalog_conn.close()
        self.send_json(result, HTTPStatus.CREATED)

    def _handle_organization_apply(self) -> None:
        body = self.read_json_body()
        user_conn = self._open_user_connection()
        catalog_conn = connect(self.database_path, read_only=True)
        try:
            collections_service.ensure_schema(user_conn)
            selected = body.get("selected") or []
            if not isinstance(selected, list):
                raise ValueError("selected 必须为列表")
            result = collections_organization.apply(
                user_conn, catalog_conn, ({} if collections_organization.get_run(user_conn, str(body.get("run_id") or ""))["operation"] == "assign" else self._local_reconcile(catalog_conn)), run_id=str(body.get("run_id") or ""), selected=selected,
            )
        finally:
            user_conn.close(); catalog_conn.close()
        self.send_json(result)

    def _handle_organization_run(self, run_id: str) -> None:
        user_conn = self._open_user_connection(read_only=True)
        try:
            self.send_json(collections_organization.get_run(user_conn, run_id))
        finally:
            user_conn.close()

    @staticmethod
    def _enrich_download_status(payload: dict) -> None:
        items = payload["items"]
        statuses: dict[str, str] = {}
        if download_store.DEFAULT_DATABASE.exists():
            conn = download_store.connect(read_only=True)
            try:
                statuses = download_store.status_map(conn, [item["entity_id"] for item in items])
            finally:
                conn.close()
        parse_statuses: dict[str, str] = {}
        index_states: dict[str, dict] = {}
        if rag_store.DEFAULT_DATABASE.exists():
            conn = rag_store.connect(read_only=True)
            try:
                parse_statuses = rag_store.status_map(conn, [item["entity_id"] for item in items])
                index_states = {item["entity_id"]: rag_store.index_state(conn, item["entity_id"]) for item in items}
            finally:
                conn.close()
        downloaded = 0
        parsed = 0
        ingested = rag_service.ingested_document_ids()
        ingested_count = 0
        for item in items:
            status = statuses.get(item["entity_id"], "")
            item["download_status"] = status
            if status in ("success", "duplicate"):
                downloaded += 1
            pstatus = parse_statuses.get(item["entity_id"], "")
            item["parse_status"] = pstatus
            if pstatus == "success":
                parsed += 1
            index_state = index_states.get(item["entity_id"], {})
            chunk_count = int(index_state.get("indexed_chunk_count") or 0)
            istatus = "success" if item["entity_id"] in ingested and chunk_count > 0 else ""
            item["ingest_status"] = istatus
            item["chunk_count"] = chunk_count
            item["parsed_sha256"] = index_state.get("parsed_sha256", "")
            item["pipeline_fingerprint"] = index_state.get("indexed_pipeline_fingerprint", "")
            if istatus:
                item["plan_status"] = "already_indexed"
            elif item.get("reason_code") == "ambiguous_match":
                item["plan_status"] = "ambiguous"
            elif status in {"success", "duplicate"} or pstatus == "success":
                item["plan_status"] = "local_ready"
            elif item.get("source") == "catalog":
                item["plan_status"] = "catalog_candidate"
            else:
                item["plan_status"] = "needs_external_resolution"
            if istatus:
                ingested_count += 1
        payload["summary"]["downloaded_count"] = downloaded
        payload["summary"]["parsed_count"] = parsed
        payload["summary"]["ingested_count"] = ingested_count

    def _handle_view_markdown(self, entity_id: str) -> None:
        path = rag_store.parsed_path(entity_id)
        if not path.exists():
            legacy = rag_store.PARSED_ROOT / f"{entity_id}.md"
            if legacy.exists():
                path = legacy
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "该论文尚未解析")
                return
        self.send_file(path.read_bytes(), "text/plain; charset=utf-8", path.name, disposition="inline")

    def _handle_download_pdf(self, entity_id: str) -> None:
        if not download_store.DEFAULT_DATABASE.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, "该论文尚未下载")
            return
        conn = download_store.connect(read_only=True)
        try:
            row = conn.execute(
                "SELECT file_path, status FROM downloads WHERE entity_id = ?", (entity_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None or row["status"] not in ("success", "duplicate") or not row["file_path"]:
            self.send_error_json(HTTPStatus.NOT_FOUND, "该论文尚未下载或文件不存在")
            return
        path = Path(row["file_path"])
        if not path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "PDF 文件不存在")
            return
        self.send_file(path.read_bytes(), "application/pdf", path.name, disposition="inline")

    def _handle_delete_download(self, entity_id: str) -> None:
        if not download_store.DEFAULT_DATABASE.exists():
            self.send_json({"entity_id": entity_id, "removed": False})
            return
        conn = download_store.connect()
        try:
            result = download_store.remove(conn, entity_id)
        finally:
            conn.close()
        self.send_json(result)

    def _handle_delete_parse(self, entity_id: str) -> None:
        if not rag_store.DEFAULT_DATABASE.exists():
            self.send_json({"entity_id": entity_id, "removed": False})
            return
        conn = rag_store.connect()
        try:
            result = rag_store.remove(conn, entity_id)
        finally:
            conn.close()
        if result.get("removed"):
            try:
                rag_service.delete_ingested(entity_id)  # cascade-delete vector-store chunks
            except Exception:
                pass
        self.send_json(result)

    def _handle_delete_ingest(self, entity_id: str) -> None:
        try:
            result = rag_service.delete_ingested(entity_id)
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"删除失败: {exc}")
            return
        self.send_json(result)

    def _handle_cleanup(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entity_ids = self._body_entity_ids(body)
        action = (body.get("action") or "").strip()
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids（请勾选论文）")
            return
        if action not in ("pdf", "parse", "ingest", "all"):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "action 必须是 pdf/parse/ingest/all")
            return

        removed = 0
        for entity_id in entity_ids:
            any_removed = False
            if action in ("pdf", "all"):
                if download_store.DEFAULT_DATABASE.exists():
                    conn = download_store.connect()
                    try:
                        any_removed = download_store.remove(conn, entity_id)["removed"] or any_removed
                    finally:
                        conn.close()
            if action in ("parse", "all"):
                if rag_store.DEFAULT_DATABASE.exists():
                    conn = rag_store.connect()
                    try:
                        any_removed = rag_store.remove(conn, entity_id)["removed"] or any_removed
                    finally:
                        conn.close()
                try:
                    rag_service.delete_ingested(entity_id)  # cascade-delete the vector store
                except Exception:
                    pass
            elif action == "ingest":
                try:
                    any_removed = rag_service.delete_ingested(entity_id)["removed"] or any_removed
                except Exception:
                    pass
            if any_removed:
                removed += 1
        self.send_json({"removed": removed, "action": action, "total": len(entity_ids)})

    def _handle_rag_ingest(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        entity_ids = self._body_entity_ids(body)
        if not entity_ids:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 entity_ids（请勾选论文）")
            return
        if not rag_store.DEFAULT_DATABASE.exists():
            self.send_error_json(HTTPStatus.BAD_REQUEST, "还没有解析任何论文，请先解析")
            return

        def job(update, is_cancelled):
            catalog_conn = connect(self.database_path, read_only=True)
            parse_conn = rag_store.connect(read_only=True)
            try:
                return rag_service.ingest_parsed(
                    catalog_conn, parse_conn, entity_ids,
                    on_progress=update, is_cancelled=is_cancelled,
                )
            finally:
                parse_conn.close()
                catalog_conn.close()

        task_id = download_tasks.start_job(job)
        self.send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)

    def _handle_rag_query(self) -> None:
        try:
            body = self.read_json_body()
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        text = (body.get("query") or "").strip()
        if not text:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "缺少 query")
            return
        try:
            top_k = int(body.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = min(max(top_k, 1), 20)
        filters = body.get("filters") if isinstance(body.get("filters"), dict) else None
        try:
            result = rag_service.query(text, top_k, filters)
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"查询失败: {exc}")
            return
        self.send_json(result)

    def _handle_rag_status(self) -> None:
        from raglib.config import load_config, load_dotenv
        try:
            load_dotenv()
            config = load_config()
            configured = bool(config.generator.base_url and config.generator.model)
            self.send_json({"status": "configured" if configured else "unconfigured",
                "error": "" if configured else "未配置模型；本地库与指定加入仍可使用。",
                "embedding_configured": bool(config.embedder.base_url and config.embedder.model and config.embedder.dim),
                "qdrant_configured": bool(config.qdrant.url)})
        except (ValueError, TypeError):
            self.send_json({"status": "unconfigured", "error": "配置格式错误，请检查模型配置"})

    def handle_static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        target = (FRONTEND_ROOT / requested).resolve()
        try:
            target.relative_to(FRONTEND_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 AIPaperbase Agent 本地数据面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    Handler.database_path = args.database.resolve()

    # Lazily build the FTS5 full-text index (auto-built on first start, skipped in seconds afterwards)
    from backend.catalog import search as fts_search
    if Handler.database_path.exists():
        try:
            conn = connect(Handler.database_path)
            try:
                if fts_search.ensure_index(conn):
                    print("FTS5 全文检索索引已自动构建（首次启动）")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # e.g. read-only environments: don't block startup, search degrades to no-FTS
            print(f"警告：FTS 索引构建失败，检索可能不可用：{exc}")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AIPaperbase Agent: http://{args.host}:{args.port}")
    print(f"Database: {Handler.database_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
