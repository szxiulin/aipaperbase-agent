#!/usr/bin/env bash
# AIPaperbase Agent 启动脚本：用项目虚拟环境运行。
# 依赖（mineru-open-sdk / qdrant-client / httpx）装在 .venv，系统 Python 不装。
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "未找到 .venv，请先执行："
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/pip install -r requirements-rag.txt"
  exit 1
fi

exec .venv/bin/python -m backend.api.server "$@"
