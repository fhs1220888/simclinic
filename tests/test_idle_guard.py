"""Mocked lifecycle contract; does not modify machine power settings."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from simclinic.evaluation.idle_guard import start_idle_guard


class IdleGuardTests(unittest.TestCase):
    def test_guard_is_scoped_to_coordinator_pid(self):
        digest = hashlib.sha256(b'Synthetic command bytes').hexdigest()
        process = Mock(pid=88)
        process.poll.return_value = None
        frozen = dict(idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256=digest))
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(Path, 'read_bytes', return_value=b'Synthetic command bytes'), \
                 patch('simclinic.evaluation.idle_guard.subprocess.Popen', return_value=process) as spawn, \
                 patch('simclinic.evaluation.idle_guard.os.getpid', return_value=77), \
                 patch('simclinic.evaluation.idle_guard.time.sleep'):
                self.assertIs(start_idle_guard(temporary, frozen, 'development'), process)
                self.assertEqual(spawn.call_args.args[0], ['/usr/bin/caffeinate', '-i', '-w', '77'])
                observed = json.loads((Path(temporary) / 'idle-guard-development.json').read_text())
                self.assertEqual(observed['coordinator_pid'], 77)
                self.assertFalse(observed['persistent_settings_changed'])

    def test_changed_binary_or_failed_guard_stops_before_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(Path, 'read_bytes', return_value=b'Synthetic bytes'):
                with self.assertRaises(ValueError):
                    start_idle_guard(temporary, dict(idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256='wrong')), 'development')
            process = Mock()
            process.poll.return_value = 1
            digest = hashlib.sha256(b'Synthetic bytes').hexdigest()
            with patch.object(Path, 'read_bytes', return_value=b'Synthetic bytes'), \
                 patch('simclinic.evaluation.idle_guard.subprocess.Popen', return_value=process), \
                 patch('simclinic.evaluation.idle_guard.time.sleep'):
                with self.assertRaises(RuntimeError):
                    start_idle_guard(temporary, dict(idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256=digest)), 'heldout')
            self.assertFalse(list(Path(temporary).glob('*.json')))
