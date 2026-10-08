"""Synthetic software tests for held evaluation; never solve held tasks here."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from simclinic.evaluation.heldout import TASKS, DEVELOPMENT, create_reference, requirement, hashes, verify_hashes, velocity_error, pressure_error, task
from simclinic.domains.openfoam.acceptance import channel_criteria
from simclinic.rendering_request import choose_field
from scripts.run_full_agent import REQUIREMENT

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT/'integration/foam_agent_v210/reference-corpus-v1/reference-case'


class HeldoutTests(unittest.TestCase):
    def test_reference_parameters_and_all_other_inputs_preserved(self):
        original = hashes(REFERENCE)
        with tempfile.TemporaryDirectory() as temporary:
            for spec in TASKS:
                destination = Path(temporary)/spec['id']
                create_reference(REFERENCE, destination, spec)
                updated = hashes(destination)
                changed = {p for p in original if original[p] != updated[p]}
                self.assertEqual(changed, {'0/U','system/blockMeshDict'} if spec['ny']==30 else {'0/U'})
                u = (destination/'0/U').read_text()
                self.assertIn(f"List<vector> {spec['ny']}", u)
                values = re.search(r'List<vector> \d+ \((.*?)\);', u, re.DOTALL).group(1)
                self.assertEqual(len(re.findall(r'\([^()]+\)', values)), spec['ny'])
        self.assertEqual(original, hashes(REFERENCE))

    def test_requirement_adapts_inputs_but_preserves_thresholds_and_sampling(self):
        for spec in TASKS:
            text = requirement(REQUIREMENT, spec)
            self.assertIn(f"mesh {spec['nx']} x {spec['ny']} x 1", text)
            self.assertIn(f"internalField uniform ({spec['mean_velocity']} 0 0)", text)
            self.assertIn('same 20 y-profile and 17 x-pressure points', text)
            self.assertIn('relative L2 error <=0.02', text)
            self.assertIn('pressure-gradient relative error <=0.03', text)
            self.assertEqual(task(spec,text)['acceptance'][:5], channel_criteria())

    def test_task_specific_analytic_reference_does_not_use_old_mean(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)/'profile.xy'
            pressure = Path(temporary)/'pressure.xy'
            spec = TASKS[1]
            profile.write_text('\n'.join(f"{(i+0.5)*0.005:.12g} {6*spec['mean_velocity']*((i+0.5)/20)*(1-(i+0.5)/20):.12g} 0 0" for i in range(20)))
            slope = -12*0.001*spec['mean_velocity']/0.1**2
            pressure.write_text('\n'.join(f"{0.1+i*0.05:.12g} {7+slope*(0.1+i*0.05):.12g}" for i in range(17)))
            self.assertLess(velocity_error(profile,spec),1e-10)
            self.assertLess(pressure_error(pressure,spec),1e-10)
            self.assertGreater(velocity_error(profile,DEVELOPMENT),1)
            self.assertGreater(pressure_error(pressure,DEVELOPMENT),1)
            profile.write_text('0.01 nan 0 0\n0.02 0 0 0')
            with self.assertRaises(ValueError):
                velocity_error(profile,spec)

    def test_frozen_hashes_reject_extra_and_modified_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'file';path.write_text('Synthetic test-only data')
            expected = hashes(temporary)
            verify_hashes(temporary,expected)
            path.write_text('changed')
            with self.assertRaises(ValueError):verify_hashes(temporary,expected)
            path.write_text('Synthetic test-only data');(Path(temporary)/'extra').write_text('extra')
            with self.assertRaises(ValueError):verify_hashes(temporary,expected)

    def test_baseline_is_actual_historical_function(self):
        path = ROOT/'integration/foam_agent_v210/native/patched/src/nodes/visualization_node.py'
        text = path.read_text()
        node = next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='_guess_primary_field')
        scope = {};exec(ast.get_source_segment(text,node),scope)
        self.assertEqual(scope['_guess_primary_field'](REQUIREMENT),'p')
        self.assertEqual(choose_field(REQUIREMENT),'U')

    def test_incomplete_run_stays_in_denominator_with_unknown_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = Path(temporary)
            for directory in ('common-runtime','corpus','variants/baseline','variants/candidate'):
                (batch/directory).mkdir(parents=True)
            frozen = dict(experiment='Synthetic software-test-only', common_runtime={}, corpus={},
                variants={'baseline':{},'candidate':{}},tasks={},schedule=[
                    dict(id='test-baseline',task='H1',repeat=1,arm='baseline'),
                    dict(id='test-candidate',task='H1',repeat=1,arm='candidate')])
            (batch/'freeze.json').write_text(json.dumps(frozen))
            (batch/'freeze.sha256').write_text(hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest())
            (batch/'progress.json').write_text(json.dumps(dict(status='running',consumed_model_tokens=0,runs=[
                dict(id='test-baseline',status='collection_or_setup_failure',directory=str(batch/'absent'))])))
            subprocess.run([sys.executable,str(ROOT/'scripts/report_heldout.py'),'--batch',str(batch)],check=True,capture_output=True)
            results = json.loads((batch/'results.json').read_text())
            group = results['groups']['baseline']
            self.assertEqual(group['executed'],1)
            self.assertEqual(group['task_failures'],1)
            self.assertEqual(group['evidence_missing'],1)
            self.assertEqual(group['behavior_metric_unknown'],1)
            self.assertIsNone(group['metrics']['model_tokens']['mean'])
            self.assertEqual(results['quality_and_evidence_eligible_pairs'],[])
            self.assertEqual(results['planned_runs'],2)
            self.assertEqual(group['planned'],1)
            self.assertEqual(group['first_execution_status_unknown'],1)
            self.assertEqual(group['input_content_rejection_unknown'],1)

    def test_pair_eligibility_uses_predeclared_task_ids_and_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = Path(temporary)
            for directory in ('common-runtime','corpus','variants/baseline','variants/candidate'):
                (batch/directory).mkdir(parents=True)
            scheduled = [dict(id='I1-r4-'+arm,task='I1',repeat=4,arm=arm) for arm in ('baseline','candidate')]
            frozen = dict(experiment='Synthetic software-test-only',inference_scope='Test-only content study scope',
                common_runtime={},corpus={},variants={'baseline':{},'candidate':{}},tasks={},schedule=scheduled)
            (batch/'freeze.json').write_text(json.dumps(frozen))
            (batch/'freeze.sha256').write_text(hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest())
            runs = []
            for entry in scheduled:
                out = batch/entry['id'];out.mkdir()
                record = json.loads((ROOT/'fixtures/healthy/record.json').read_text())
                shutil.copy2(ROOT/'fixtures/healthy/case/results.json',out/'results.json')
                (out/'record.json').write_text(json.dumps(record))
                values = {c['id']:c['target'] for c in record['task']['acceptance']}
                summary = dict(acceptance=values,quality_met=True,evidence_complete=True,
                    checks={'error_continuation':'pass','completion':'pass','retries':'not_applicable'},
                    metrics=record['metrics'],model_usage={'total_calls':0},audit_summary={},failure=None)
                (out/'summary.json').write_text(json.dumps(summary))
                runs.append(dict(entry,status='finished',directory=str(out)))
            (batch/'progress.json').write_text(json.dumps(dict(status='complete',consumed_model_tokens=0,runs=runs)))
            process = subprocess.run([sys.executable,str(ROOT/'scripts/report_heldout.py'),'--batch',str(batch)],capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            result = json.loads((batch/'results.json').read_text())
            self.assertEqual(result['inference_scope'],frozen['inference_scope'])
            self.assertEqual(result['quality_and_evidence_eligible_pairs'],[{'task':'I1','repeat':4}])
            self.assertEqual(result['groups']['baseline']['planned'],1)


if __name__ == '__main__':
    unittest.main()
