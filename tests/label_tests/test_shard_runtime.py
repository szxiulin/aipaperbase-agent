"""Validate worker I/O and monitoring without launching any models."""
import importlib.util
import hashlib
import json
import os
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/label'))
import shard_io
import run_codex_shards as runner


class ShardRuntimeTest(unittest.TestCase):
    def setUp(self):
        # Retain generated fixtures for inspection; no file deletion is needed.
        self.shard = Path(tempfile.mkdtemp(prefix='apex-shard-runtime-'))
        self.papers = [dict(entity_id=f'e{i}', venue='ICML', year=2025, title='Example',
                            abstract='A study of language model evaluation.') for i in range(2)]
        (self.shard / 'papers.jsonl').write_text('\n'.join(json.dumps(p) for p in self.papers))
        self.answer = [1, 'llm', '', 'high', 'language model evaluation', [], [], ['text'], '', 'method']

    def test_append_copies_exact_identity(self):
        self.assertEqual(shard_io.append(self.shard, json.dumps([self.answer])), 1)
        row = json.loads((self.shard / 'labels.jsonl').read_text())
        self.assertEqual(row['entity_id'], 'e0')
        self.assertEqual(row['area'], 'llm')

    def test_next_repeats_review_guidance_without_assigning_labels(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['shard_io', 'next', str(self.shard), '1']), redirect_stdout(output):
            shard_io.main()
        self.assertIn('## e0 | ICML 2025 | row=1', output.getvalue())
        self.assertIn('ABSTRACT: A study of language model evaluation.', output.getvalue())
        self.assertIn(shard_io.REVIEW_REMINDER, output.getvalue())
        self.assertFalse((self.shard / 'labels.jsonl').exists())

    def test_batch_validation_is_atomic(self):
        invalid = [2, 'invented', '', 'high', 'invalid label example', [], [], ['text'], '', 'method']
        with self.assertRaises(ValueError):
            shard_io.append(self.shard, json.dumps([self.answer, invalid]))
        self.assertEqual((self.shard / 'labels.jsonl').read_text(), '')

    def test_duplicate_batch_is_rejected(self):
        shard_io.append(self.shard, json.dumps([self.answer]))
        before = (self.shard / 'labels.jsonl').read_bytes()
        with self.assertRaises(ValueError):
            shard_io.append(self.shard, json.dumps([self.answer]))
        self.assertEqual((self.shard / 'labels.jsonl').read_bytes(), before)

    def test_complete_ids_with_missing_fields_do_not_pass(self):
        (self.shard / 'labels.jsonl').write_text('\n'.join(json.dumps({'entity_id': p['entity_id']}) for p in self.papers))
        self.assertFalse(runner.valid(self.shard))

    def test_all_complete_valid_rows_pass(self):
        second = [2, *self.answer[1:]]
        shard_io.append(self.shard, json.dumps([self.answer, second]))
        self.assertTrue(runner.valid(self.shard))

    def test_missing_abstract_requires_low_confidence(self):
        row = dict(zip(shard_io.FIELDS, ['e0', *self.answer[1:]]))
        paper = dict(self.papers[0], abstract='(no abstract available)')
        self.assertIn('missing abstract requires low confidence', shard_io.validate(row, paper, shard_io.vocabulary(self.shard)))

    def test_invalid_task_and_modality_rejected(self):
        row = dict(zip(shard_io.FIELDS, ['e0', *self.answer[1:]]))
        row.update(task='Study', modality=['smell'])
        errors = shard_io.validate(row, self.papers[0], shard_io.vocabulary(self.shard))
        self.assertIn('invalid task phrase', errors)
        self.assertIn('invalid modality', errors)

    def test_unhashable_scalar_returns_validation_error(self):
        row = dict(zip(shard_io.FIELDS, ['e0', *self.answer[1:]]))
        row['area'] = []
        self.assertEqual(shard_io.validate(row, self.papers[0], shard_io.vocabulary(self.shard)), ['scalar fields must be strings'])

    def test_rate_limit_detection_avoids_paper_numbers(self):
        self.assertIsNone(runner.LIMIT.search('We evaluate 429 examples in a rate limited network.'))
        self.assertIsNotNone(runner.LIMIT.search('unexpected status 429 Too Many Requests'))
        self.assertIsNotNone(runner.LIMIT.search('rate_limit_exceeded'))

    def test_wallet_error_is_fatal_service_failure(self):
        line = 'ERROR: ' + json.dumps({'error': {'type': 'insufficient_balance',
                                                'code': 'enterprise_insufficient_balance'}})
        self.assertEqual(runner.service_failure(line), 'provider_insufficient_balance')
        self.assertIsNone(runner.service_failure('ABSTRACT: insufficient_balance error'))
        self.assertIsNone(runner.service_failure('ERROR: not JSON'))

    def test_account_runtime_ignores_third_party_home_and_api_overrides(self):
        overrides = {name: 'fixture-override' for name in runner.API_OVERRIDES}
        overrides['CODEX_HOME'] = '/tmp/unwanted-provider-home'
        with patch.dict(os.environ, overrides):
            binary, env = runner.account_runtime()
        self.assertEqual(env['CODEX_HOME'], str(Path.home() / '.codex'))
        self.assertTrue(binary)
        for name in runner.API_OVERRIDES:
            self.assertNotIn(name, env)

    def test_api_login_is_rejected_without_printing_credentials(self):
        result = Mock(returncode=0, stdout='Logged in using an API key', stderr='')
        with patch.object(runner.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'Official ChatGPT login is required'):
                runner.require_chatgpt_login('fixture-codex', {})
        self.assertIn('forced_login_method="chatgpt"', run.call_args.args[0])

    def test_chatgpt_login_is_accepted(self):
        result = Mock(returncode=0, stdout='Logged in using ChatGPT', stderr='')
        with patch.object(runner.subprocess, 'run', return_value=result):
            runner.require_chatgpt_login('fixture-codex', {})

    def test_worker_command_uses_account_configured_mcp_names(self):
        command = runner.worker_command('fixture-codex', 'gpt-5.6-sol', 'medium')
        self.assertIn('model_provider="openai"', command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertNotIn('mcp_servers.myquant_local.enabled=false', command)

    def test_cli_config_error_stops_dispatch(self):
        self.assertEqual(runner.service_failure('Error loading config.toml: invalid transport'),
                         'worker_configuration_error')

    def test_legacy_provider_state_cannot_be_resumed_as_account_labels(self):
        prompt = '- llm: Language models\nmodality: from ["text"]\ncontribution_type: one of ["method"]\n'
        (self.shard / 'labeling_prompt.md').write_text(prompt)
        previous = dict(model='gpt-5.6-sol', effort='medium',
                        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        (self.shard / 'pipeline_state.json').write_text(json.dumps(previous))
        with patch.object(sys, 'argv', ['runner', str(self.shard), '--only', str(self.shard)]), \
                patch.object(runner, 'require_chatgpt_login'), \
                patch.object(runner.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(SystemExit, 'never mix provider results'):
                runner.main()
        popen.assert_not_called()
        self.assertEqual(json.loads((self.shard / 'pipeline_state.json').read_text()), previous)

    def test_account_usage_limit_stops_instead_of_retrying(self):
        self.assertEqual(runner.service_failure('ERROR: {"error":{"type":"usage_limit_reached"}}'),
                         'codex_account_usage_limit')
        self.assertEqual(runner.service_failure('ERROR: You have hit your usage limit.'),
                         'codex_account_usage_limit')

    def test_wallet_error_stops_dispatch_without_retry(self):
        work = Path(tempfile.mkdtemp(prefix='apex-blocked-runtime-'))
        shard = work / 'shard_0001'
        shard.mkdir()
        (shard / 'papers.jsonl').write_text((self.shard / 'papers.jsonl').read_text())
        (work / 'labeling_prompt.md').write_text(
            '- llm: Language models\nmodality: from ["text"]\n'
            'contribution_type: one of ["method"]\n')
        process = Mock(pid=99999999)
        process.poll.return_value = 1
        process.wait.return_value = 1

        def launch(*args, **kwargs):
            self.assertIn('model_provider="openai"', args[0])
            self.assertIn('forced_login_method="chatgpt"', args[0])
            self.assertEqual(kwargs['env']['CODEX_HOME'], str(Path.home() / '.codex'))
            kwargs['stdout'].write('ERROR: {"error":{"type":"insufficient_balance"}}\n')
            kwargs['stdout'].flush()
            return process

        with patch.object(sys, 'argv', ['runner', str(work), '--poll', '0.001']), \
                patch.object(runner.subprocess, 'Popen', side_effect=launch) as popen, \
                patch.object(runner, 'require_chatgpt_login'), \
                patch.object(runner.os, 'killpg'):
            self.assertEqual(runner.main(), 2)
        state = json.loads((work / 'pipeline_state.json').read_text())
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(state['phase'], 'blocked_external_service')
        self.assertEqual(state['blocked_reason'], 'provider_insufficient_balance')
        self.assertEqual(state['attempts'], {'shard_0001': 1})
        self.assertEqual(state['active'], {})
        self.assertEqual(state['provider'], 'openai')
        self.assertEqual(state['auth_method'], 'chatgpt')
        self.assertFalse((shard / 'labels.jsonl').exists())

    def test_shutdown_permission_error_does_not_skip_cleanup(self):
        process = Mock(pid=99999999)
        process.poll.return_value = 0
        output = Mock()
        with patch.object(runner.os, 'killpg', side_effect=PermissionError('denied')):
            self.assertEqual(runner.stop_jobs({'one': {'process': process, 'output': output}}), set())
        process.terminate.assert_called_once()
        output.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
