You are the orchestrator for labeling 121,742 AI papers. You do NOT label papers yourself: worker Codex sessions do that, one 500-paper shard each, launched by an existing script. Your job is to keep that pipeline running fast until every shard is done, then merge the results.

Repository root: the current directory. Read `scripts/label/CODEX_TASK.md` first (the "全量" section) to understand the setup.

1. Preflight
   - Check that `data/analyses/codex_full/` has 244 `shard_NNNN/` directories and `labeling_prompt.md`. If not, export them: `.venv/bin/python scripts/label/label_papers.py --all --export data/analyses/codex_full --shard-size 500`.
   - Each worker runs `codex exec`, which needs network access. Confirm that this session can run it; if the sandbox blocks it, ask me to restart you with a less restrictive sandbox, and do not work around it.

2. Launch in the background so it survives your turns:
   `PARALLEL=8 nohup scripts/label/run_codex_shards.sh data/analyses/codex_full > data/analyses/codex_full/run.log 2>&1 &`

3. Monitor about every 10 minutes until finished:
   - Progress: `grep -c "done " data/analyses/codex_full/run.log` and `ls data/analyses/codex_full/shard_*/labels.jsonl | wc -l`.
   - Speed: from the timestamps, report shards per hour and the estimated remaining time.
   - Rate limits: if worker logs (`shard_*/codex.log`) show rate-limit or 429 errors, stop the run (kill the script and its xargs/codex children) and relaunch with `PARALLEL=4`. If shards finish cleanly and there are no limit errors after the first hour, you may relaunch with `PARALLEL=12` for more speed. Relaunching is safe: finished shards are skipped and stale locks are cleared.
   - Quality spot check after the first 3 finished shards: run `python3 scripts/label/shard_io.py status <shard>` on each, and read 10 random lines of one `labels.jsonl` together with the matching papers. Make sure the labels look reasoned: the area matches the paper, the task is specific, and the papers do not all get the same label. If workers are cutting corners (scripted or uniform labels), stop and tell me.

4. When the script exits:
   - If it reports incomplete shards, rerun the same launch command, at most 3 more times. If shards still fail, read their `codex.log` and report why.
   - When all 244 are complete, merge:
     `.venv/bin/python scripts/label/label_papers.py --ingest data/analyses/codex_full/shard_*/labels.jsonl --model codex-<the model the workers used> --out data/labels/areas-v1.2.csv`
     Expect 121,742 rows with status ok. Report the status counts and the area distribution.

Rules: do not edit code, prompts or `config/`; do not modify or delete any `labels.jsonl` by hand except to remove an exact duplicate line; do not commit or push. Report back in Chinese, briefly, only at milestones: launched, first quality check, any PARALLEL change, finished.
