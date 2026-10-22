"""Capability protocol: one registry, many entry points (HTTP, MCP, web agent).

A capability is a name + JSON Schema + handler(connection, args) -> data. The registry wraps every
result with the label-release meta so a number can always be traced to a data version. Handlers are
pure read-only functions; they never write and never call an LLM.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.analytics import areas as area_queries

SCHEMA_VERSION = "capabilities-v1"


@dataclass
class Capability:
    name: str
    description: str
    parameters: dict
    handler: Callable[[sqlite3.Connection, dict], Any]
    category: str = "meta"
    returns: str = ""

    def tool_schema(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.parameters}


@dataclass
class CapabilityResult:
    ok: bool
    data: Any = None
    error: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = {"ok": self.ok, "data": self.data, "meta": self.meta}
        if not self.ok:
            payload["error"] = self.error
        return payload


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        self._caps = {cap.name: cap for cap in capabilities}

    def names(self) -> list[str]:
        return list(self._caps)

    def list(self) -> list[dict]:
        return [{"name": c.name, "description": c.description, "parameters": c.parameters,
                 "category": c.category, "returns": c.returns} for c in self._caps.values()]

    def tools(self) -> list[dict]:
        """MCP-shaped tool list (inputSchema instead of parameters)."""
        return [{"name": c.name, "description": c.description, "inputSchema": c.parameters}
                for c in self._caps.values()]

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def meta(self, connection: sqlite3.Connection) -> dict:
        release = area_queries.latest_label_release(connection)
        meta = {"schema_version": SCHEMA_VERSION,
                "label_release_id": release["label_release_id"] if release else "",
                "areas_version": release["areas_version"] if release else "",
                "directions_version": release["directions_version"] if release else "",
                "denominator": "canonical research entities with area assignments, excluding non-paper entities",
                "classification_note": "Area and direction coverage differ. Papers can have an area while their direction remains pending; full pending classification is included in totals but not named areas."}
        if release:
            try:
                metrics = json.loads(release["metrics_json"] or "{}")
            except json.JSONDecodeError:
                metrics = {}
            if metrics.get("area"):
                meta["area_accuracy"] = metrics["area"].get("accuracy")
                meta["reviewer"] = metrics.get("reviewer", "")
                meta["accuracy_scope"] = "Historical 399-paper AI-agent review; not independent human accuracy and not an evaluation of the new area-only cohort."
        return meta

    def call(self, connection: sqlite3.Connection, name: str, arguments: dict | None) -> CapabilityResult:
        cap = self._caps.get(name)
        if cap is None:
            return CapabilityResult(ok=False, error=f"unknown capability: {name}", meta=self.meta(connection))
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return CapabilityResult(ok=False, error="arguments must be a JSON object", meta=self.meta(connection))
        try:
            data = cap.handler(connection, arguments)
            return CapabilityResult(ok=True, data=data, meta=self.meta(connection))
        except ValueError as exc:
            return CapabilityResult(ok=False, error=str(exc), meta=self.meta(connection))
        except Exception as exc:  # never break the adapter on a bad query
            return CapabilityResult(ok=False, error=f"{type(exc).__name__}: {exc}", meta=self.meta(connection))
