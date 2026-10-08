"""Independent reviewer checks with software processes; no CFD/model calls."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts import review_recovery_observation as review
from simclinic.domains.openfoam import acceptance, input_contract, logs, reference_tasks
from simclinic.checks import behavior
from tests import test_recovery_observer as observer_fixtures
from tests import test_reference_tasks as reference_fixtures


class RecoveryObservationReviewTests(unittest.TestCase):
    def fixture(self, root, *, enabled=False, scenario='natural', modes=('valid',)):
        helper = observer_fixtures.RecoveryObserverTests()
        policy, case, context, calls = helper.setup_case(root, enabled=enabled, scenario=scenario, modes=modes)
        recorder = policy.recorder
        recorder.data['environment']['execution_id'] = 'software-execution'
        recorder.capture_attempt(case, context)
        _, error, summary = helper.execute(policy, case, context)
        items, _, issues = review.artifacts(recorder.directory, recorder.data)
        self.assertFalse(issues)
        row = dict(id='software-run', execution_id='software-execution', arm='candidate' if enabled else 'baseline',
                   stage='injected' if scenario != 'natural' else 'natural', **context,
                   **{k: summary[k] for k in ('first_sample', 'recovery', 'injection')})
        native = review.read(recorder.directory / 'attempts' / context['run_id'] / 'native/record.json')
        readers = dict(logs=logs, reference=reference_tasks, spec=dict(id='C0', kind='couette', wall_speed=.02, nx=40, ny=20))
        return recorder, row, items, readers, native, error

    def test_healthy_first_call_and_no_branch(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, row, items, readers, native, _ = self.fixture(root)
            checked = review.sample_review(row, recorder.data, items, readers, native)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertFalse(checked['missing_output_confirmed_from_raw'])
            self.assertEqual(checked['registered_sample_count'], 1)

    def test_shared_before_recovery_and_fresh_extra_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, row, items, readers, native, error = self.fixture(root, enabled=True, modes=('missing', 'valid'))
            self.assertIsNone(error)
            checked = review.sample_review(row, recorder.data, items, readers, native)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertTrue(checked['trusted_source']['verified'])
            self.assertTrue(checked['fresh_second_workspace_verified'])
            self.assertTrue(checked['recovery_branch_cost_verified'])

    def test_full_intervention_reader_and_two_original_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, row, items, readers, native, _ = self.fixture(root, enabled=True, scenario='transient_missing')
            checked = review.sample_review(row, recorder.data, items, readers, native)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertTrue(checked['injection_original_full_gate_verified'])
            self.assertTrue(checked['injection_source']['verified'])

    def test_unknown_sampler_encoding_is_honest_unknown_not_review_failure(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, row, items, readers, native, _ = self.fixture(root, enabled=True, modes=('encoding',))
            checked = review.sample_review(row, recorder.data, items, readers, native)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertFalse(checked['missing_output_confirmed_from_raw'])
            self.assertEqual(checked['bounded_decision_count'], 0)

    def test_baseline_retry_or_natural_injection_are_rejected(self):
        for mutate in ('baseline', 'natural'):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as root:
                recorder, row, items, readers, native, _ = self.fixture(root, enabled=True, scenario='transient_missing')
                row['arm' if mutate == 'baseline' else 'stage'] = 'baseline' if mutate == 'baseline' else 'natural'
                checked = review.sample_review(row, recorder.data, items, readers, native)
                self.assertEqual(checked['status'], 'fail')

    def test_first_native_identity_cannot_be_filled_from_later_case(self):
        with tempfile.TemporaryDirectory() as root:
            recorder, row, items, readers, native, _ = self.fixture(root)
            row['run_id'] = 'wrong-run'
            checked = review.sample_review(row, recorder.data, items, readers, native)
            self.assertEqual(checked['status'], 'fail')

    def test_salvage_unknown_usage_retains_known_lower_bound(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            for number, value in enumerate((123, None)):
                folder = path / 'model-calls' / str(number)
                folder.mkdir(parents=True)
                (folder / 'metadata.json').write_text(json.dumps(dict(total_tokens=value, outcome='pass')))
            (path / 'model-calls' / 'started-only').mkdir()
            checked = review.raw_models(path)
            self.assertEqual(checked['known_model_tokens_lower_bound'], 123)
            self.assertIsNone(checked['model_tokens'])
            self.assertEqual(checked['unknown_usage_calls'], 2)
            self.assertFalse(checked['exhaustive_total_proven'])

    def test_partial_without_record_never_infers_final_success(self):
        with tempfile.TemporaryDirectory() as root:
            row = dict(id='started', execution_id='execution')
            checked = review.review_run(Path(root), row, row, {}, Path(root), False)
            self.assertEqual(checked['status'], 'incomplete')
            self.assertFalse(checked['success_inferred'])

    def test_uncaptured_raw_case_metadata_is_salvage_only_and_copies_not_summed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            original = path / 'case/.simclinic/native/later-run/command-calls/one/metadata.json'
            copy = path / 'attempts/later-run/native/command-calls/one/metadata.json'
            for destination in (original, copy):
                destination.parent.mkdir(parents=True)
                destination.write_text(json.dumps(dict(run_id='later-run', input_version='untrusted', phase='solve', elapsed_seconds=2)))
            sealed = review.raw_process_metadata(path, {})
            self.assertEqual(len(sealed), 1)
            salvage = review.raw_process_metadata(path, {}, include_case=True)
            self.assertEqual(len(salvage), 2)
            self.assertTrue(any(item['mutable_case_raw_salvage'] for item in salvage))
            self.assertTrue(all(item['native_identity_used_to_fill_final_record'] is False for item in salvage))
            self.assertTrue(all(item['counted_as_distinct_process'] is False for item in salvage))

    def test_artifact_tampering_is_reported_without_erasing_other_items(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / 'good').write_bytes(b'good')
            (path / 'bad').write_bytes(b'changed')
            record = dict(artifacts=[dict(id='good', path='good', sha256=hashlib.sha256(b'good').hexdigest()),
                                     dict(id='bad', path='bad', sha256=hashlib.sha256(b'old').hexdigest())])
            items, inventory, issues = review.artifacts(path, record)
            self.assertEqual(set(items), {'good'})
            self.assertEqual(len(inventory), 2)
            self.assertEqual(len(issues), 1)

    def test_fresh_output_refuses_archive_and_old_reports(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root).resolve()
            batch, freeze = path / 'batch', path / 'freeze'
            batch.mkdir(); freeze.mkdir()
            (freeze / 'freeze.sha256').write_text('sealed')
            for target in (batch / 'independent.json', freeze / 'independent.json'):
                with self.assertRaises(ValueError):
                    review.fresh_output(target, batch, freeze)
            report = path / 'review.json'
            self.assertEqual(review.fresh_output(report, batch, freeze), report)
            report.write_text('{}')
            with self.assertRaises(ValueError):
                review.fresh_output(report, batch, freeze)

    def acceptance_fixture(self, root):
        path = Path(root).resolve()
        spec = dict(id='software-C7', kind='couette', wall_speed=.024, nx=58, ny=29)
        template = Path(__file__).resolve().parents[1] / 'integration/foam_agent_v210/reference-corpus-v1/reference-case'
        reference = reference_tasks.create_reference(template, path / 'reference', spec)
        directory = path / 'archive'
        snapshot = directory / 'attempts/native-run/native/input-snapshot'
        shutil.copytree(reference, snapshot)
        context = dict(run_id='native-run', input_version='sha256:software-input')
        items, entries = {}, []
        def save(relative, value, *, raw=False):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if raw:
                target.write_bytes(value)
            else:
                target.write_text(json.dumps(value))
            item = dict(id='a' + str(len(items)), path=relative, sha256=review.sha(target), **context)
            entries.append(item)
            items[item['id']] = dict(item, file=target)
            return item['id']
        log = directory / 'attempts/native-run/logs/log.simpleFoam'
        log.parent.mkdir(parents=True)
        log.write_text('SIMPLE solution converged\ntime step continuity errors : sum local = 0, global = 0\nEnd\n')
        native_path = directory / 'attempts/native-run/native/record.json'
        native_path.write_text(json.dumps(dict(**context, output_times=[], artifacts=[])))
        sample_root = directory / 'observations/sampling'
        sample_root.mkdir(parents=True)
        paths = reference_fixtures.ReferenceTaskTests().samples(sample_root, spec)
        outputs = []
        for sample in paths:
            identifier = save(str(sample.relative_to(directory)), sample.read_bytes(), raw=True)
            outputs.append(dict(path=str(sample.relative_to(directory)), artifact_id=identifier, sha256=items[identifier]['sha256']))
        measurements = reference_tasks.reference_measurements(*paths, spec)
        gate_id = save('observations/sampling/gate-assessment.json', dict(**context, outcome='pass', outputs=outputs, measurements=measurements))
        save('attempts/native-run/outputs/U.png', b'\x89PNG\r\n\x1a\nsoftware-only-image', raw=True)
        save('observations/node-visualization.json', dict(stage='end', node='visualization', payload=dict(
            case_dir='/unused-mutable-case', plot_outputs=['/unused-mutable-case/U.png'],
            pyvista_visualization=dict(success=True), visualization_summary=dict(fields_visualized=['U']))))
        task = input_contract.contract_task(spec)
        checks, _ = input_contract.compare_case(snapshot, reference, spec)
        values = dict(execution_end=True, steady_evidence=True, local_continuity=0.0, task_inputs_verified=True,
                      rendered_velocity=True, **measurements)
        save('observations/task-assessment.json', dict(**context, criteria=task['acceptance'], input_checks=checks, values=values, error=None))
        record = dict(**context, collection_status='complete', artifacts=entries,
                      attempts=[dict(**context, execution=dict(exit_code=0), input_snapshot=str(snapshot.relative_to(directory)))],
                      events=[dict(id='sample', operation='sample', outcome='pass', source_artifact=gate_id),
                              dict(id='complete', kind='claim', claim_type='completion', success=True)])
        row = dict(**context, quality_met=True, acceptance_verified=True, files={str(log.relative_to(directory)): review.sha(log)})
        readers = dict(reference=reference_tasks, acceptance=acceptance, inputs=input_contract, checks=behavior)
        definition = dict(spec=spec, task=task, reference=reference)
        return directory, row, record, items, readers, definition, paths

    def test_full_acceptance_uses_only_archived_samples_log_inputs_and_png(self):
        with tempfile.TemporaryDirectory() as root:
            directory, row, record, items, readers, definition, _ = self.acceptance_fixture(root)
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertTrue(checked['full_quality'])
            self.assertFalse(checked['mutable_case_reopened'])

    def test_changed_raw_reference_measurement_cannot_reuse_asserted_success(self):
        with tempfile.TemporaryDirectory() as root:
            directory, row, record, items, readers, definition, paths = self.acceptance_fixture(root)
            paths[1].write_text(paths[1].read_text().replace('0.012 0.0 0.0', '0.024 0.0 0.0'))
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'fail', checked)


if __name__ == '__main__':
    unittest.main()
