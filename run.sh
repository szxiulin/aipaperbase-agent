#!/usr/bin/env bash
# AIPaperbase Agent launcher: creates the project virtualenv on first run, builds the local
# paper catalog from the committed CSVs, then starts the web app on http://127.0.0.1:8765.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PYTHON_OK=""
  for candidate in ${PYTHON:-} python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PYTHON_OK="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON_OK" ]; then
    echo "需要 Python 3.12 或更高版本（Python 3.12+ is required）。"
    echo "安装后重试，或用 PYTHON=/path/to/python3.12 ./run.sh 指定。"
    exit 1
  fi
  PYTHON="$PYTHON_OK"
  echo "首次运行：创建 .venv（First run: creating .venv）…"
  "$PYTHON" -m venv .venv
fi

if ! .venv/bin/python -c 'import httpx' 2>/dev/null; then
  echo "安装对话所需依赖（Installing chat dependency: httpx）…"
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || echo "警告：httpx 安装失败，论文库和领域地图仍可用，对话不可用。"
fi

if [ ! -f data/database/catalog.sqlite ]; then
  echo "首次启动：从 data/catalog 构建论文库（约 2–5 分钟，需约 1 GB 空间）…"
  echo "First start: building the paper catalog (about 2–5 minutes, ~1 GB)…"
  .venv/bin/python -m backend.catalog.import_csv
fi

exec .venv/bin/python -m backend.api.server "$@"
