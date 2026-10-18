#!/usr/bin/env python3
"""Resumable local Codex orchestration. Never assigns or edits paper labels."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from shard_io import load

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT_AUTH = ['-c', 'model_provider="openai"', '-c', 'forced_login_method="chatgpt"']
API_OVERRIDES = ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_ACCESS_TOKEN',
                 'OPENAI_IDENTITY_TOKEN_FILE', 'OPENAI_FEDERATION_RULE_ID')
LIMIT = re.compile(
    r'(?:http(?:/\S+)?|status(?: code)?|error(?: code)?)\s*[:=]?\s*429\b'
    r'|\b429\s+too many requests|\brate_limit_(?:exceeded|error|reached)\b'
    r'|\brate limit (?:reached|exceeded)|\busage_limit_reached\b|\bquota exceeded\b', re.I)


def account_runtime():
    """Use the existing official account login without opening credential files."""
    official_home = Path.home() / '.codex'
    packaged = official_home / 'packages/standalone/current/bin/codex'
    binary = str(packaged) if packaged.is_file() else shutil.which('codex')
    if not binary:
        raise RuntimeError('Codex CLI is not installed')
    env = os.environ.copy()
    env['CODEX_HOME'] = str(official_home)
    for name in API_OVERRIDES:
        env.pop(name, None)
    return binary, env


def require_chatgpt_login(binary, env):
    result = subprocess.run([binary, *ACCOUNT_AUTH, 'login', 'status'], env=env,
                            capture_output=True, text=True, timeout=30)
    if result.returncode or 'Logged in using ChatGPT' not in result.stdout + result.stderr:
        # Never print login output: API-key status can contain a key fragment.
        raise RuntimeError('Official ChatGPT login is required; no API/provider fallback is allowed')


def worker_command(binary, model, effort):
    return [binary, 'exec', '--ephemeral', '-s', 'workspace-write', '-m', model, *ACCOUNT_AUTH,
            '-c', f'model_reasoning_effort="{effort}"', '--disable', 'plugins',
            '--disable', 'apps', '--disable', 'memories', '--disable', 'multi_agent',
            '--enable', 'skip_host_skill_discovery',
            '-c', 'mcp_servers.alphaxiv.enabled=false',
            '-c', 'mcp_servers.computer-use.enabled=false', '-c', 'mcp_servers.node_repl.enabled=false']


def service_failure(text):
    """Recognize explicit provider errors, never words inside paper abstracts."""
    for line in text.splitlines():
        if line.startswith('Error loading config.toml:'):
            return 'worker_configuration_error'
        if not line.startswith('ERROR: '):
            continue
        if re.search(r'\b(?:you have hit|you have reached) your usage limit\b', line, re.I):
            return 'codex_account_usage_limit'
        try:
            error = json.loads(line[7:]).get('error', {})
        except (ValueError, AttributeError):
            continue
        if not isinstance(error, dict):
            continue
        codes = {v for v in (error.get('code'), error.get('type')) if isinstance(v, str)}
        if codes & {'insufficient_balance', 'enterprise_insufficient_balance'}:
            return 'provider_insufficient_balance'
        if codes & {'usage_limit_reached', 'insufficient_quota'}:
            return 'codex_account_usage_limit'
    return None


def log(message):
    print(f'[{datetime.now(ZoneInfo("Asia/Shanghai")):%F %T}] {message}', flush=True)


def valid(shard):
    papers, counts, problems = load(shard)
    wanted = {p['entity_id'] for p in papers}
    return not problems and set(counts) == wanted and all(n == 1 for n in counts.values())


def stop_jobs(jobs):
    for job in jobs.values():
        try:
            os.killpg(job['process'].pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Some hosts deny group signals while allowing signals to our child.
            try:
                job['process'].terminate()
            except (ProcessLookupError, PermissionError) as error:
                log(f'could not terminate worker {job["process"].pid}: {error}')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(j['process'].poll() is None for j in jobs.values()):
        time.sleep(.1)
    for job in jobs.values():
        if job['process'].poll() is None:
            try:
                os.killpg(job['process'].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    job['process'].kill()
                except (ProcessLookupError, PermissionError) as error:
                    log(f'could not kill worker {job["process"].pid}: {error}')
        try:
            job['process'].wait(timeout=2)
        except subprocess.TimeoutExpired:
            log(f'worker still alive; manual stop required: {job["process"].pid}')
        job['output'].close()
    return {j['process'].pid for j in jobs.values() if j['process'].poll() is None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', nargs='?', default='data/analyses/codex_full_account_v2', type=Path)
    parser.add_argument('--only', type=Path)
    parser.add_argument('--poll', type=float, default=5)
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    os.chdir(ROOT)
    work = args.work.resolve()
    shards = [args.only.resolve()] if args.only else sorted(work.glob('shard_[0-9][0-9][0-9][0-9]'))
    if not shards:
        sys.exit('no shards found')
    binary, worker_env = account_runtime()
    require_chatgpt_login(binary, worker_env)
    run_lock = (work / 'pipeline.lock').open('a+')
    try:
        fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit('another orchestrator already owns this work directory')
    model = os.environ.get('CODEX_MODEL') or 'gpt-5.6-sol'
    effort = os.environ.get('CODEX_EFFORT') or 'medium'
    parallel = int(os.environ.get('PARALLEL', '8'))
    if not 1 <= parallel <= 12:
        sys.exit('PARALLEL must be between 1 and 12')
    prompt_file = work / 'labeling_prompt.md'
    prompt_hash = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
    protocol = (ROOT / 'scripts/label/codex_shard_prompt.md').read_text()
    protocol_hash = hashlib.sha256(protocol.encode()).hexdigest()
    state_file = work / 'pipeline_state.json'
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    if previous and any(previous.get(k) != v for k, v in [('model', model), ('effort', effort),
            ('prompt_sha256', prompt_hash), ('provider', 'openai'), ('auth_method', 'chatgpt'),
            ('protocol_sha256', protocol_hash)]):
        run_lock.close()
        sys.exit('model/effort/rules/protocol/account route differs: use a separate work directory; never mix provider results')
    attempts = previous.get('attempts', {})
    completed = {p for p in shards if valid(p)}
    pending = deque(p for p in shards if p not in completed and attempts.get(p.name, 0) < 4)
    jobs = {}
    started = time.time()
    initial_done = len(completed)
    rate_seen = bool(previous.get('rate_limit_seen', False))
    rate_events = int(previous.get('rate_limit_events', 0))
    if rate_seen:
        parallel = min(parallel, 4)
    gate = len(shards) >= 3
    gate_file = work / 'quality_gate.json'
    phase = 'running'
    blocked_reason = None
    cli = worker_command(binary, model, effort)

    def approved():
        if not gate:
            return True
        if not gate_file.exists():
            return False
        review = json.loads(gate_file.read_text())
        return (review.get('approved') is True and review.get('prompt_sha256') == prompt_hash
                and len(review.get('reviewed_shards', [])) >= 3)

    def save():
        elapsed = max(time.time() - started, 1)
        rate = (len(completed) - initial_done) * 3600 / elapsed
        result = dict(pid=os.getpid(), model=model, effort=effort, prompt_sha256=prompt_hash,
                      provider='openai', auth_method='chatgpt', cli_binary=binary,
                      codex_home=worker_env['CODEX_HOME'],
                      protocol_sha256=hashlib.sha256(protocol.encode()).hexdigest(),
                      parallel=parallel, phase=phase, completed=len(completed), total=len(shards),
                      blocked_reason=blocked_reason,
                      completed_shards=sorted(p.name for p in completed),
                      active={p.name: j['process'].pid for p, j in jobs.items()},
                      attempts=attempts, rate_limit_seen=rate_seen, rate_limit_events=rate_events,
                      started_at=started,
                      updated_at=time.time(), shards_per_hour=round(rate, 2),
                      remaining_hours=round((len(shards)-len(completed))/rate, 2) if rate else None)
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(state_file)

    def interrupted(signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    log(f'launched {parallel} workers; openai/ChatGPT account; {model}/{effort}; {len(completed)}/{len(shards)} already complete')
    last_save = 0
    last_progress = 0
    try:
        while pending or jobs:
            for shard, job in list(jobs.items()):
                path = shard / 'codex.log'
                with path.open('r', errors='replace') as stream:
                    stream.seek(job['offset'])
                    text = stream.read()
                    job['offset'] = stream.tell()
                recent = job.get('tail', '') + text
                failure = service_failure(recent)
                if failure:
                    phase = 'blocked_external_service'
                    blocked_reason = failure
                    log(f'{failure} in {shard.name}; stopping owned workers; no further retries')
                    return 2
                if LIMIT.search(recent):
                    rate_events += 1
                    if rate_events >= 3:
                        phase = 'blocked_external_service'
                        blocked_reason = 'repeated_rate_limits'
                        log('three rate-limit events reached; stopping owned workers; no endless retries')
                        return 2
                    log(f'rate limit detected in {shard.name}; stopping owned workers; PARALLEL=4')
                    rate_seen = True
                    remaining = stop_jobs(jobs)
                    if remaining:
                        phase = 'blocked_worker_shutdown'
                        blocked_reason = 'unable_to_stop_owned_workers'
                        return 2
                    for p in jobs:
                        attempts[p.name] -= 1
                        pending.appendleft(p)
                    jobs.clear()
                    parallel = 4
                    phase = 'rate_limit_backoff'
                    save()
                    time.sleep(60)
                    phase = 'running'
                    break
                job['tail'] = recent[-4096:]
                process = job['process']
                if process.poll() is None and time.time() - job['start'] > args.timeout:
                    log(f'timeout {shard.name}; preserving partial labels for retry')
                    stop_jobs({shard: job})
                if process.poll() is None:
                    continue
                job['output'].close()
                del jobs[shard]
                if valid(shard):
                    completed.add(shard)
                    log(f'done {shard.name} ({len(completed)}/{len(shards)})')
                elif attempts[shard.name] < 4:
                    pending.append(shard)
                    log(f'retry queued {shard.name}; attempt {attempts[shard.name]}/4')
                else:
                    log(f'FAIL {shard.name}; four attempts exhausted; see codex.log')
            need_review = gate and len(completed) >= 3 and not approved()
            if need_review and phase != 'quality_review':
                phase = 'quality_review'
                log('first three shards ready; waiting for semantic spot-check approval before scheduling more')
            elif not need_review:
                phase = 'running'
            if not rate_seen and parallel == 8 and time.time()-started >= 3600 and approved():
                parallel = 12
                log('first hour clean; PARALLEL=12')
            while pending and len(jobs) < parallel and not need_review:
                shard = pending.popleft()
                if valid(shard):
                    completed.add(shard)
                    continue
                total = sum(bool(s.strip()) for s in (shard / 'papers.jsonl').read_text().splitlines())
                prompt = protocol.replace('{SHARD_DIR}', str(shard)).replace('{PROMPT_FILE}', str(prompt_file)).replace('{TOTAL}', str(total))
                output = (shard / 'codex.log').open('a', encoding='utf-8')
                offset = output.tell()
                process = subprocess.Popen([*cli, prompt], stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT,
                                           cwd=ROOT, env=worker_env, start_new_session=True)
                attempts[shard.name] = attempts.get(shard.name, 0) + 1
                jobs[shard] = dict(process=process, output=output, offset=offset, start=time.time())
                log(f'start {shard.name} attempt {attempts[shard.name]} pid={process.pid}')
            if time.time()-last_save >= 30:
                save()
                last_save = time.time()
            if time.time()-last_progress >= 600:
                log(f'progress {len(completed)}/{len(shards)} complete; {len(jobs)} active; PARALLEL={parallel}')
                last_progress = time.time()
            time.sleep(args.poll)
        if len(completed) != len(shards):
            phase = 'incomplete'
            save()
            log('incomplete shards remain; see pipeline_state.json and corresponding codex.log')
            return 1
        if gate and not approved():
            phase = 'quality_review'
            save()
            log('all current workers complete; quality approval is still required before merge')
            return 1
        if len(shards) == 244 and not args.only:
            ids = [p['entity_id'] for shard in shards for p in load(shard)[0]]
            if len(ids) != 121742 or len(set(ids)) != 121742:
                raise ValueError('full input does not contain exactly 121742 unique papers')
            destination = ROOT / 'data/labels/areas-v1.2.csv'
            if destination.exists():
                raise ValueError('merge destination already exists; refusing to overwrite')
            python = str(ROOT / '.venv/bin/python')
            subprocess.run([python, 'scripts/label/label_papers.py', '--ingest',
                            *[str(p / 'labels.jsonl') for p in shards], '--model', f'codex-{model}',
                            '--out', str(destination)], check=True)
            import csv
            with destination.open() as stream:
                merged = list(csv.DictReader(stream))
            statuses = Counter(r['status'] for r in merged)
            if len(merged) != 121742 or statuses != {'ok': 121742} or {r['entity_id'] for r in merged} != set(ids):
                raise ValueError(f'merged output failed validation: {statuses}')
            log(f'merged: {dict(statuses)}; areas={dict(Counter(r["area"] for r in merged))}')
        phase = 'finished'
        save()
        log(f'finished {len(completed)}/{len(shards)}')
        return 0
    except KeyboardInterrupt:
        phase = 'stopped'
        log('stopping owned workers; all written labels retained')
        return 130
    finally:
        try:
            remaining = stop_jobs(jobs)
            jobs = {p: j for p, j in jobs.items() if j['process'].pid in remaining}
        finally:
            try:
                save()
            finally:
                run_lock.close()


if __name__ == '__main__':
    sys.exit(main())
