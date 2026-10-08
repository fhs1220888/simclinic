"""Synthetic failed CLI streams; never authenticate or invoke a model here."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simclinic.backends.cli_diagnostics import failure_diagnostic
from simclinic.backends.codex_cli import CodexCLI


class CLIDiagnosticsTests(unittest.TestCase):
    def test_error_categories_without_raw_diagnostics(self):
        cases = [(429, 'model_rate_limit'), (401, 'model_auth_error'), (503, 'model_service_error'), (400, 'model_request_error')]
        for status, category in cases:
            observed = failure_diagnostic(json.dumps(dict(type='turn.failed', error=dict(status=status, message='SECRET TEST ONLY'))), '')
            self.assertEqual(observed['category'], category)
            self.assertNotIn('SECRET', json.dumps(observed))
        self.assertEqual(failure_diagnostic('', 'stream disconnected: bearer SECRET')['category'], 'model_transport_error')
        self.assertEqual(failure_diagnostic('', 'Unclassified SECRET')['category'], 'model_unknown_error')

    def test_failed_exit_preserves_reported_usage_without_retries_or_secrets(self):
        class Process:
            returncode = 1
            def communicate(self, prompt, timeout):
                return '\n'.join(json.dumps(e) for e in [
                    dict(type='item.completed', item=dict(type='reasoning', text='SECRET REASONING')),
                    dict(type='turn.completed', usage=dict(input_tokens=20, cached_input_tokens=10, output_tokens=4)),
                    dict(type='turn.failed', error=dict(message='HTTP error 429 SECRET CREDENTIAL'))]), 'SECRET STDERR'
        with tempfile.TemporaryDirectory() as temporary:
            with patch('simclinic.backends.codex_cli.subprocess.check_output', return_value='test-cli'), \
                 patch('simclinic.backends.codex_cli.subprocess.Popen', return_value=Process()) as popen:
                backend = CodexCLI(temporary, 'test-model')
                with self.assertRaisesRegex(RuntimeError, 'model_rate_limit'):
                    backend.invoke('Synthetic test prompt')
                self.assertEqual(popen.call_count, 1)
            self.assertEqual(backend.calls[0]['total_tokens'], 24)
            self.assertEqual(backend.calls[0]['error_category'], 'model_rate_limit')
            for file in Path(temporary).rglob('*.json'):
                self.assertNotIn('SECRET', file.read_text())

    def test_missing_failed_usage_stays_unknown(self):
        class Process:
            returncode = 1
            def communicate(self, prompt, timeout):
                return json.dumps(dict(type='error', message='stream disconnected before completion')), ''
        with tempfile.TemporaryDirectory() as temporary:
            with patch('simclinic.backends.codex_cli.subprocess.check_output', return_value='test-cli'), \
                 patch('simclinic.backends.codex_cli.subprocess.Popen', return_value=Process()):
                backend = CodexCLI(temporary, 'test-model')
                with self.assertRaisesRegex(RuntimeError, 'model_transport_error'):
                    backend.invoke('Synthetic test prompt')
                self.assertIsNone(backend.calls[0]['total_tokens'])
                self.assertIsNone(backend.get_statistics()['total_tokens'])


if __name__ == '__main__':
    unittest.main()
