#!/usr/bin/env bash
# Label one shard with fresh non-interactive Codex sessions (called by run_codex_shards.sh).
# Usage: label_one_shard.sh SHARD_DIR WORK_DIR
set -eu
dir=$1; work=$2
cd "$(dirname "$0")/../.."
exec python3 scripts/label/run_codex_shards.py "$work" --only "$dir"
