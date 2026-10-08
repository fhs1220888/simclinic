import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

from simclinic.evaluation.recovery_observation_driver import (
    BudgetBoundary, Ledger, budget_reason, digest, offline_contract_checks,
    stop_reason, verify_batch, verify_registered_evidence, writable_new, write_new)
from scripts.run_recovery_observation import guarded_backend, coordinate
from scripts import run_recovery_observation as driver_script

ROOT = Path(__file__).resolve().parents[1]


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'protocols/bounded-recovery-observation-v1/protocol.json').read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve()
        self.sha = '1' * 64

    def ledger(self):
        return Ledger(self.path / 'ledger.jsonl', self.config, self.sha, create=True)

    def test_start_survives_missing_final_and_keeps_known_usage(self):
        ledger = self.ledger()
        first = ledger.start(self.config['entries'][0])
        call = self.path / 'runs' / first['entry']['id'] / 'model-calls/call-001/metadata.json'
        write_new(call, dict(total_tokens=17))
        write_new(self.path / 'progress.json', dict(status='collection_failure', freeze_sha256=self.sha))
        verified = verify_batch(self.path, self.config, self.sha)
        self.assertEqual(verified['all_started'], 1)
        self.assertIsNone(verified['rows'][0]['quality_met'])
        self.assertIsNone(verified['rows'][0]['consumption']['model_tokens'])
        self.assertEqual(verified['rows'][0]['consumption_known']['model_tokens'], 17)
        self.assertFalse(verified['run_bytes_verified'])

    def test_ledger_rejects_overlap_selective_order_and_reused_execution(self):
        ledger = self.ledger()
        with self.assertRaises(ValueError):
            ledger.start(self.config['entries'][1])
        start = ledger.start(self.config['entries'][0])
        with self.assertRaises(ValueError):
            ledger.start(self.config['entries'][1])
        ledger.finish(dict(id=start['entry']['id'], execution_id=start['execution_id']))
        with self.assertRaises(ValueError):
            ledger.append('STARTED', entry=self.config['entries'][1], execution_id=start['execution_id'])

    def test_ledger_tamper_and_partial_line_rejected(self):
        ledger = self.ledger()
        ledger.start(self.config['entries'][0])
        path = self.path / 'ledger.jsonl'
        data = json.loads(path.read_text())
        data['entry']['arm'] = 'candidate'
        path.write_text(json.dumps(data) + '\n')
        with self.assertRaises(ValueError):
            Ledger(path, self.config, self.sha)
        path.write_text('{"sequence":1')
        with self.assertRaises(ValueError):
            Ledger(path, self.config, self.sha)

    def test_unknown_sampler_is_honest_outcome_not_collection_gap(self):
        row = dict(stage='natural', quality_met=False, evidence_verified=True,
                   consumption=dict(model_tokens=0), coverage=dict(tools=False, references=False),
                   first_sample=dict(function_called=True, launch_status='started',
                                     missing_output_confirmed=None, eligible=None))
        self.assertIsNone(stop_reason(row))
        self.assertEqual(stop_reason(dict(row, evidence_verified=False)), 'collection_failure')
        self.assertEqual(stop_reason(dict(row, backend_failure=True)), 'backend_failure')
        self.assertEqual(stop_reason(dict(row, consumption=dict(model_tokens=None))), 'usage_unknown')
        self.assertEqual(stop_reason(dict(row, stage='development')), 'development_quality_failure')
        self.assertEqual(stop_reason(dict(row, identity_unverified=True)), 'identity_or_policy_violation')
        self.assertEqual(stop_reason(dict(row, stage='injected', injection=dict(status='not_applied'))),
                         'noncomparable_intervention')

    def test_soft_budget_exact_reserve_stage_no_borrow_and_wall(self):
        limits = self.config['budgets']
        stage = 'development'
        tokens = limits['stage_token_boundaries'][stage] - limits['reserve_before_run']
        rows = [dict(stage=stage, consumption=dict(model_tokens=tokens))]
        self.assertIsNone(budget_reason(self.config, rows, stage, 0, 0))
        rows[0]['consumption']['model_tokens'] += 1
        self.assertEqual(budget_reason(self.config, rows, stage, 0, 0), 'budget_boundary')
        self.assertIsNone(budget_reason(self.config, rows, 'natural', 0, 0))
        self.assertEqual(budget_reason(self.config, [], 'natural', 0, limits['stage_wall_seconds']['natural']),
                         'budget_boundary')
        self.assertEqual(budget_reason(self.config, [dict(stage='natural', consumption=dict(model_tokens=None))],
                                       'natural', 0, 0), 'usage_unknown')

    def test_model_guard_checks_after_inflight_overshoot_and_before_next(self):
        class Base:
            max_calls = 32
            def __init__(self):
                self.calls = []
            def invoke(self):
                self.calls.append(dict(total_tokens=100))
                return 'response'
        guarded = guarded_backend(Base, lambda calls: 'budget_boundary' if len(calls) else None)()
        with self.assertRaises(BudgetBoundary):
            guarded.invoke()
        self.assertEqual(len(guarded.calls), 1)
        with self.assertRaises(BudgetBoundary):
            guarded.invoke()
        self.assertEqual(len(guarded.calls), 1)

    def test_frozen_and_existing_output_never_written(self):
        with self.assertRaises(ValueError):
            writable_new(self.path)
        frozen = self.path / 'moved-archive'
        frozen.mkdir()
        (frozen / 'freeze.sha256').write_text(self.sha)
        with self.assertRaises(ValueError):
            writable_new(frozen / 'new-batch')
        with self.assertRaises(ValueError):
            coordinate(self.path / 'batch', self.path / 'bundle', authorized=False)
        self.assertFalse((self.path / 'batch').exists())

    def test_artifact_bytes_references_and_escaping_rejected(self):
        file = self.path / 'evidence.txt'
        file.write_text('retained failure')
        record = dict(artifacts=[dict(id='a1', path='evidence.txt',
                      sha256=hashlib.sha256(file.read_bytes()).hexdigest())],
                      events=[dict(source_artifact='a1')])
        self.assertEqual(verify_registered_evidence(self.path, record), 1)
        file.write_text('edited')
        with self.assertRaises(ValueError):
            verify_registered_evidence(self.path, record)
        record['artifacts'][0]['path'] = '../outside'
        with self.assertRaises(ValueError):
            verify_registered_evidence(self.path, record)

    def test_complete_requires_all_sealed_runs(self):
        ledger = self.ledger()
        ledger.start(self.config['entries'][0])
        write_new(self.path / 'progress.json', dict(status='complete', freeze_sha256=self.sha))
        with self.assertRaises(ValueError):
            verify_batch(self.path, self.config, self.sha)

    def test_offline_contract_check_uses_actual_frozen_schedule(self):
        result = offline_contract_checks(self.config)
        self.assertTrue(result['budget_verified'])
        altered = copy.deepcopy(self.config)
        altered['entries'][4], altered['entries'][53] = altered['entries'][53], altered['entries'][4]
        with self.assertRaises(ValueError):
            offline_contract_checks(altered)

    def test_registered_child_writes_honest_presample_failure_and_scorer_counts_it(self):
        # This exercises the actual new child/archive/scorer path, with an
        # offline graph and no backend invocation. It is not CFD evidence.
        bundle = self.path / 'bundle'
        batch = self.path / 'batch'
        runtime = bundle / 'common-runtime'
        runtime.mkdir(parents=True)
        (bundle / 'native').mkdir()
        (bundle / 'native/source.txt').write_text('offline fixture')
        from simclinic.evaluation.heldout import hashes
        write_new(bundle / 'freeze.json', dict(native=hashes(bundle / 'native'), design_freeze_sha256='fixture-design'))
        spec = self.config['development_tasks'][0]
        definition = ROOT / 'protocols/bounded-recovery-observation-v1/definitions' / spec['id']
        import shutil
        shutil.copytree(definition, bundle / 'definitions' / spec['id'])
        settings = dict(model='fixture-model', image='fixture-image', resources={},
                        agent_commit='fixture', codex_executable='fixture-no-launch')
        write_new(bundle / 'environment.json', dict(configured=settings))
        write_new(bundle / 'protocol-design/protocol.json', self.config)
        batch.mkdir()
        ledger = Ledger(batch / 'ledger.jsonl', self.config, self.sha, create=True)
        start = ledger.start(self.config['entries'][0])
        write_new(batch / 'progress.json', dict(status='running', freeze_sha256=self.sha, rows=[],
                  elapsed_total_seconds=0, elapsed_stage_seconds=0, idle_guard_pid=123))
        write_new(batch / 'authorization.json', dict(explicit_cli_authorization=True, freeze_sha256=self.sha))
        main = ModuleType('main')
        def create_graph(observer, completion_policy):
            def invoke(state, config):
                recorder = completion_policy.recorder
                assessment = dict(run_id=None, input_version=None, accepted=False,
                    criteria=recorder.data['task']['acceptance'], values={}, error=dict(type='ValueError', code='pre_sample_task_failure'))
                file = recorder.directory / 'observations/task-assessment.json'
                write_new(file, assessment)
                source = recorder.artifact(file)
                claim = dict(success=False, status='failed', emitter='offline fixture', assessment_artifact=source)
                result = dict(completion_claim=claim, acceptance_report=assessment)
                observer('end', 'completion', result)
                return result
            return SimpleNamespace(compile=lambda: SimpleNamespace(invoke=invoke))
        main.create_foam_agent_graph = create_graph
        main.initialize_state = lambda *args, **kwargs: {}
        config_module = ModuleType('config')
        config_module.Config = lambda **kwargs: SimpleNamespace(**kwargs)
        services = ModuleType('services')
        services.configure_llm = lambda value: None
        utils = ModuleType('utils')
        utils.configure_retriever = lambda value: None
        logger = ModuleType('logger')
        logger.close_logging = lambda: None
        class OfflineBackend:
            def __init__(self, *args, **kwargs):
                self.calls = []
                self.max_calls = 32
            def get_statistics(self):
                return dict(total_calls=0, total_tokens=0, model_cost=None)
            def invoke(self, *args, **kwargs):
                raise AssertionError('No model invocation in offline integration')
        with patch.object(driver_script, 'ROOT', runtime), \
             patch.object(driver_script, 'freeze_data', return_value=({}, self.sha, {})), \
             patch.dict('sys.modules', {'main': main, 'config': config_module, 'services': services,
                                       'utils': utils, 'logger': logger}), \
             patch('simclinic.backends.codex_cli.CodexCLI', OfflineBackend), \
             patch('simclinic.retrieval.LexicalReferences', return_value=object()):
            self.assertEqual(driver_script.run_one(batch, bundle, start['entry']['id']), 0)
        row = json.loads((batch / 'runs' / start['entry']['id'] / 'summary.json').read_text())
        self.assertFalse(row['quality_met'])
        self.assertTrue(row['evidence_verified'])
        self.assertEqual(row['consumption']['model_tokens'], 0)
        ledger.finish(row)
        from simclinic.core.report import write_json
        write_json(batch / 'progress.json', dict(status='development_quality_failure', freeze_sha256=self.sha, rows=[row]))
        write_new(batch / 'test-note.json', dict(synthetic=True))
        verified = verify_batch(batch, self.config, self.sha)
        from simclinic.evaluation.recovery_observation_score import score_observations
        score = score_observations(self.config['entries'], verified['rows'], started=verified['started'])
        self.assertEqual(score['groups']['development/baseline']['counts']['N'], 1)
        self.assertEqual(score['groups']['development/baseline']['counts']['S'], 0)
        self.assertEqual(score['comparisons']['natural']['recovery_benefit_status'], 'not_assessable')

    def test_dispatch_failure_keeps_start_and_releases_temporary_guard(self):
        bundle = self.path / 'bundle'
        runtime = bundle / 'common-runtime'
        runtime.mkdir(parents=True)
        write_new(bundle / 'freeze.json', dict(native={}, design_freeze_sha256='offline-fixture'))
        write_new(bundle / 'protocol-design/protocol.json', self.config)
        batch = self.path / 'batch'
        class Guard:
            pid = 987
            alive = True
            def poll(self):
                return None if self.alive else 0
            def terminate(self):
                self.alive = False
            def wait(self, timeout):
                return 0
        guard = Guard()
        with patch.object(driver_script, 'ROOT', runtime), \
             patch.object(driver_script, 'freeze_data', return_value=({}, self.sha, {})), \
             patch('simclinic.evaluation.idle_guard.start_idle_guard', return_value=guard), \
             patch.object(driver_script.subprocess, 'run', side_effect=OSError('offline dispatch failure')):
            self.assertEqual(coordinate(batch, bundle, authorized=True), 2)
        self.assertFalse(guard.alive)
        self.assertTrue(json.loads((batch / 'idle-guard-released.json').read_text())['released'])
        verified = verify_batch(batch, self.config, self.sha, bundle=bundle)
        self.assertEqual(verified['all_started'], 1)
        self.assertIsNone(verified['rows'][0]['quality_met'])
        self.assertEqual(verified['status'], 'collection_failure')
        self.assertGreaterEqual(verified['rows'][0]['consumption_known']['workflow_elapsed_seconds'], 0)
        scored = json.loads((batch / 'results.json').read_text())['scoring']
        self.assertEqual(scored['started'], 1)


if __name__ == '__main__':
    unittest.main()
