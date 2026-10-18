"""Model / web-search settings editable from the web UI.

Values live in the repository-root .env (gitignored) next to the other provider settings, so the
CLI, MCP server and web app read the same configuration. Only whitelisted keys can be written,
API keys are never returned in full, and changes apply to the running process immediately.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
_LOCK = threading.Lock()
_LOADED = False

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_SUGGESTIONS = ["deepseek-flash", "deepseek-v4-pro"]

FIELDS = {
    # key: (env name, default)
    "base_url": ("GENERATOR_BASE_URL", DEEPSEEK_BASE_URL),
    "model": ("GENERATOR_MODEL", "deepseek-flash"),
    "api_key": ("GENERATOR_API_KEY", ""),
    "thinking": ("GENERATOR_THINKING", "enabled"),
    "reasoning_effort": ("GENERATOR_REASONING_EFFORT", "high"),
    "max_tokens": ("GENERATOR_MAX_TOKENS", "8192"),
    "web_search": ("WEB_SEARCH_ENABLED", "false"),
    "web_search_api_key": ("WEB_SEARCH_API_KEY", ""),
}
SECRET_FIELDS = {"api_key", "web_search_api_key"}


def _validate(field: str, value: str) -> str:
    value = (value or "").strip()
    if field == "base_url":
        if not re.fullmatch(r"https?://[A-Za-z0-9.\-:]+(/[A-Za-z0-9._\-/]*)?", value):
            raise ValueError("Base URL 必须是 http(s) 地址")
        return value.rstrip("/")
    if field == "model":
        if not re.fullmatch(r"[A-Za-z0-9._:/\-]{1,80}", value):
            raise ValueError("模型名只能包含字母、数字和 . _ : / -")
        return value
    if field in SECRET_FIELDS:
        if value and not re.fullmatch(r"[\x21-\x7e]{8,256}", value):
            raise ValueError("API Key 格式不正确")
        return value
    if field == "thinking":
        if value not in ("enabled", "disabled"):
            raise ValueError("thinking 只能是 enabled / disabled")
        return value
    if field == "reasoning_effort":
        if value not in ("low", "high", "max"):
            raise ValueError("思考强度只能是 low / high / max")
        return value
    if field == "max_tokens":
        if not value.isdigit() or not 256 <= int(value) <= 65536:
            raise ValueError("最大输出 token 需在 256–65536 之间")
        return value
    if field == "web_search":
        return "true" if value.lower() in ("1", "true", "yes", "on") else "false"
    raise ValueError(f"不支持的设置项: {field}")


def ensure_loaded() -> None:
    """Load .env into os.environ once (existing environment variables win)."""
    global _LOADED
    if not _LOADED:
        from raglib.config import load_dotenv
        load_dotenv(str(ENV_PATH))
        _LOADED = True


def _current(field: str) -> str:
    env_name, default = FIELDS[field]
    return os.environ.get(env_name, default) or default if field not in SECRET_FIELDS else os.environ.get(env_name, "")


def current(field: str) -> str:
    """Effective value of a setting, falling back to the same defaults the settings page shows."""
    ensure_loaded()
    return _current(field)


def _mask(value: str) -> str:
    return f"…{value[-4:]}" if value else ""


def is_deepseek(base_url: str) -> bool:
    return "api.deepseek.com" in (base_url or "")


def web_search_key() -> str:
    """Key for DeepSeek Search/Fetch: a dedicated key, else the chat key when the chat model is DeepSeek."""
    ensure_loaded()
    dedicated = os.environ.get("WEB_SEARCH_API_KEY", "")
    if dedicated:
        return dedicated
    if is_deepseek(_current("base_url")):
        return os.environ.get("GENERATOR_API_KEY", "")
    return ""


def public_settings() -> dict:
    ensure_loaded()
    data = {field: _current(field) for field in FIELDS if field not in SECRET_FIELDS}
    data["web_search"] = data["web_search"] == "true"
    data["api_key_set"] = bool(os.environ.get("GENERATOR_API_KEY"))
    data["api_key_hint"] = _mask(os.environ.get("GENERATOR_API_KEY", ""))
    data["web_search_key_set"] = bool(web_search_key())
    data["web_search_key_source"] = ("dedicated" if os.environ.get("WEB_SEARCH_API_KEY") else
                                     "chat" if web_search_key() else "")
    data["configured"] = bool(data["api_key_set"] and data["base_url"] and data["model"])
    data["model_suggestions"] = MODEL_SUGGESTIONS
    data["env_path"] = ".env"
    return data


def _write_env(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if name in updates:
            out.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            out.append(line)
    missing = [name for name in updates if name not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# Written by the web settings panel")
        out.extend(f"{name}={updates[name]}" for name in missing)
    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ENV_PATH)


def update(values: dict) -> dict:
    """Validate and persist the given fields. An empty secret means "keep the current key"
    unless clear_api_key / clear_web_search_api_key is true."""
    ensure_loaded()
    if not isinstance(values, dict):
        raise ValueError("设置必须是 JSON 对象")
    env_updates: dict[str, str] = {}
    for field, raw in values.items():
        if field in ("clear_api_key", "clear_web_search_api_key"):
            if raw is True:
                target = "api_key" if field == "clear_api_key" else "web_search_api_key"
                env_updates[FIELDS[target][0]] = ""
            continue
        if field not in FIELDS:
            raise ValueError(f"不支持的设置项: {field}")
        value = "true" if raw is True else "false" if raw is False else str(raw if raw is not None else "")
        if field in SECRET_FIELDS and not value.strip():
            continue
        env_updates[FIELDS[field][0]] = _validate(field, value)
    with _LOCK:
        if env_updates:
            _write_env(env_updates)
            for name, value in env_updates.items():
                os.environ[name] = value
    return public_settings()


def test_connection(values: dict | None = None) -> dict:
    """List models from the provider (free on DeepSeek) to check the URL and key."""
    ensure_loaded()
    values = values or {}
    base_url = _validate("base_url", values.get("base_url") or _current("base_url"))
    key = (values.get("api_key") or "").strip() or os.environ.get("GENERATOR_API_KEY", "")
    if not key:
        return {"ok": False, "error": "还没有填写 API Key"}
    import httpx  # only needed for the connection test; keeps server start-up dependency-free

    try:
        response = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"连接失败：{exc}"}
    if response.status_code == 401:
        return {"ok": False, "error": "API Key 无效（401）"}
    if response.status_code >= 400:
        return {"ok": False, "error": f"服务返回 HTTP {response.status_code}"}
    try:
        models = [item.get("id") for item in response.json().get("data", []) if item.get("id")]
    except ValueError:
        models = []
    return {"ok": True, "models": models[:30]}
