"""Synthetic protocol tests only; no model or solver execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from simclinic.evaluation.heldout import DEVELOPMENT, task, requirement, create_reference
from simclinic.evaluation.content_study import TASKS as OLD
from simclinic.evaluation.sampling_study import (TASKS, POLICIES, completion_class, development_ready,
                                                schedule, validate_new_tasks, verify_sampling_freeze)
from scripts.run_full_agent import REQUIREMENT

ROOT = Path(__file__).resolve().parents[1]


class SamplingStudyTests(unittest.TestCase):
    def test_new_tasks_and_counterbalanced_schedule(self):
        validate_new_tasks()
        with self.assertRaises(ValueError):
            validate_new_tasks([dict(OLD[0], id='J1')])
        with self.assertRaises(ValueError):
            validate_new_tasks([TASKS[0], dict(TASKS[0], id='J4')])
        planned = schedule()
        self.assertEqual(len(planned), 18)
        self.assertEqual(len({e['id'] for e in planned}), 18)
        for spec in TASKS:
            for repeat in (1, 2, 3):
                self.assertEqual({e['arm'] for e in planned if e['task'] == spec['id'] and e['repeat'] == repeat}, set(POLICIES))
        self.assertNotEqual(planned[0]['arm'], planned[6]['arm'])

    def test_policy_selector_is_only_declared_difference(self):
        self.assertEqual(completion_class('baseline').__name__, 'HeldoutCompletion')
        self.assertEqual(completion_class('candidate').__name__, 'GatedHeldoutCompletion')
        with self.assertRaises(ValueError):
            completion_class('unknown')

    def test_new_reference_preserves_physics_sampling_and_thresholds(self):
        template = ROOT / 'integration/foam_agent_v210/reference-corpus-v1/reference-case'
        with tempfile.TemporaryDirectory() as temporary:
            for spec in TASKS:
                destination = Path(temporary) / spec['id']
                create_reference(template, destination, spec)
                for name in ('system/sample', 'system/fvSolution', 'constant/physicalProperties'):
                    self.assertEqual((template / name).read_bytes(), (destination / name).read_bytes())
                objective = requirement(REQUIREMENT, spec)
                self.assertIn(f"mesh {spec['nx']} x {spec['ny']} x 1", objective)
                self.assertIn('relative L2 error <=0.02', objective)
                self.assertEqual(task(spec, objective)['acceptance'], task(DEVELOPMENT, REQUIREMENT)['acceptance'])

    def test_unknown_or_failed_development_cannot_open_held_stage(self):
        frozen = dict(development_model_token_limit=1000)
        progress = dict(development=[dict(id='D0-' + arm, status='finished', quality_met=True,
                         evidence_complete=True, model_tokens=100) for arm in POLICIES], runs=[], consumed_model_tokens=200)
        self.assertTrue(development_ready(progress, frozen))
        for field, value in (('quality_met', False), ('evidence_complete', False), ('model_tokens', None), ('status', 'running')):
            altered = json.loads(json.dumps(progress))
            altered['development'][1][field] = value
            self.assertFalse(development_ready(altered, frozen))
        progress['runs'] = [dict(id='already-started')]
        self.assertFalse(development_ready(progress, frozen))

    def test_budget_and_usage_are_required_before_held_launch(self):
        progress = dict(development=[dict(id='D0-' + arm, status='finished', quality_met=True,
                        evidence_complete=True, model_tokens=600) for arm in POLICIES], runs=[], consumed_model_tokens=1200)
        self.assertFalse(development_ready(progress, dict(development_model_token_limit=1000)))
        progress['consumed_model_tokens'] = 100
        self.assertFalse(development_ready(progress, dict(development_model_token_limit=2000)))

    def test_native_difference_or_mutated_protocol_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            b = Path(temporary)
            for name in ('common-runtime', 'corpus', 'variants/baseline', 'variants/candidate'):
                (b / name).mkdir(parents=True)
            (b / 'requirements.lock').write_text('Synthetic test only')
            (b / 'arm-difference.patch').write_text('Synthetic selector declaration')
            frozen = dict(experiment='registered-sampling-gate-v1', completion_policies=POLICIES,
                          common_runtime={}, corpus={}, tasks={}, variants=dict(baseline={}, candidate={}),
                          requirements_sha256=hashlib.sha256((b / 'requirements.lock').read_bytes()).hexdigest(),
                          arm_difference_sha256=hashlib.sha256((b / 'arm-difference.patch').read_bytes()).hexdigest())
            def save():
                (b / 'freeze.json').write_text(json.dumps(frozen))
                (b / 'freeze.sha256').write_text(hashlib.sha256((b / 'freeze.json').read_bytes()).hexdigest())
            save()
            verify_sampling_freeze(b)
            frozen['variants']['candidate'] = {'extra.py': 'invalid'}
            save()
            with self.assertRaises(ValueError):
                verify_sampling_freeze(b)
            frozen['variants']['candidate'] = {}
            save()
            (b / 'freeze.json').write_text('{}')
            with self.assertRaises(ValueError):
                verify_sampling_freeze(b)


if __name__ == '__main__':
    unittest.main()
