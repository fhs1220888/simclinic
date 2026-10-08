"""Offline protocol guards, with no environment, CFD or model execution."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_recovery_observation_protocol import unused, verify_design

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'protocols/bounded-recovery-observation-v1'


class ObservationProtocolTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'real-runs/full-model-contract-recovery-study-20261006/freeze.json').is_file(),
                         'Optional historical design basis; see docs/archive-access.md')
    def test_design_checks_frozen_matrix_and_original_quality_without_execution(self):
        result = verify_design(PROTOCOL)
        self.assertEqual(result['counts']['total'], 76)
        self.assertEqual(result['counts']['natural'], 48)
        self.assertEqual(result['counts']['injected'], 24)
        self.assertFalse(result['execution_ready'])
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['new_model_calls'], 0)
        self.assertEqual(result['new_simulations'], 0)

    def test_hidden_mutated_input_counts_as_used_even_with_new_task_id(self):
        task = dict(id='renamed', wall_speed=.022, nx=48, ny=23)
        catalog = dict(scan_complete=True, errors=[], combinations=[dict(wall_speed=.022, nx=48, ny=23)])
        with self.assertRaisesRegex(ValueError, 'previously used'):
            unused([task], catalog)

    def test_incomplete_scan_and_unclassified_partial_do_not_establish_novelty(self):
        task = dict(wall_speed=.024, nx=58, ny=29)
        for catalog in (dict(scan_complete=False, errors=[], combinations=[]),
                        dict(scan_complete=True, errors=['unreadable'], combinations=[]),
                        dict(scan_complete=True, errors=[], combinations=[], partial_inputs=[dict(wall_speed=None)])):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                unused([task], catalog)

    def test_registered_single_file_fixture_retains_unknown_and_explicit_scope(self):
        catalog = dict(scan_complete=True, errors=[], combinations=[],
                       partial_inputs=[dict(registered_single_file_fixture=True, wall_speed=None, nx=None, ny=None)])
        unused([dict(wall_speed=.024, nx=58, ny=29)], catalog)
        self.assertIsNone(catalog['partial_inputs'][0]['wall_speed'])

    def test_unsealed_design_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'freeze.json').write_text(json.dumps(dict(status='execution_ready')))
            Path(directory, 'freeze.sha256').write_text('unknown')
            with self.assertRaisesRegex(ValueError, 'freeze changed'):
                verify_design(directory)


if __name__ == '__main__':
    unittest.main()
