#!/usr/bin/env bash
# Label every exported shard with parallel Codex workers; each shard gets fresh sessions.
# Resumable: finished shards are skipped, so rerun the same command after any stop.
#
# Usage (repo root):
#   scripts/label/run_codex_shards.sh [WORK_DIR]
# Environment:
#   PARALLEL=8            concurrent Codex sessions (default 8)
#   CODEX_MODEL=<model>   model passed to codex exec (default: gpt-5.6-sol)
#   CODEX_EFFORT=medium   reasoning effort (default: medium)
# Uses ~/.codex official ChatGPT account login; API/provider fallback is forbidden.
set -eu
cd "$(dirname "$0")/../.."
exec python3 scripts/label/run_codex_shards.py "${1:-data/analyses/codex_full_account_v2}"
