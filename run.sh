#!/usr/bin/env bash
# AIPaperbase Agent 启动脚本：用项目虚拟环境运行。
# 基础目录只需 Python；RAG 依赖按 README 安装到 .venv。
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "未找到 .venv，请先执行："
  echo "  python3 -m venv .venv"
  exit 1
fi

if [ ! -f data/database/catalog.sqlite ]; then
  echo "首次启动：正在从 data/catalog 构建公共论文目录（约 2–5 分钟，需约 1 GB 空间）…"
  .venv/bin/python -m backend.catalog.import_csv
fi

exec .venv/bin/python -m backend.api.server "$@"
