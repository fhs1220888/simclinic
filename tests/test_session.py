"""Synthetic software-contract tests; no fake data is used as CFD evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.checks.behavior import audit,error_continuation,retries
from simclinic.core.record import Record,RecordError,validate
from simclinic.session import SessionRecorder
from simclinic.domains.openfoam.completion import ChannelCompletion,task_definition
from simclinic.openfoam_observer import main as observer_main

ROOT=Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def test_zero_exit_without_mesh_verdict_stops_at_actual_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            case=Path(temporary)/'case';native=case/'.simclinic/native';native.mkdir(parents=True)
            (case/'Allrun').write_text('Test-only fixture')
            (native/'current.json').write_text(json.dumps(dict(run_id='c'*32,input_version='sha256:test')))
            output=io.TextIOWrapper(io.BytesIO());errors=io.TextIOWrapper(io.BytesIO())
            result=subprocess.CompletedProcess(['test-only-checkMesh'],0,b'No mesh verdict\n',b'')
            with patch('simclinic.openfoam_observer.Path',return_value=case),\
                 patch('simclinic.openfoam_observer.sys.argv',['observer','checkMesh']),\
                 patch('simclinic.openfoam_observer.shutil.which',return_value='test-only-checkMesh'),\
                 patch('simclinic.openfoam_observer.subprocess.run',return_value=result),\
                 patch('simclinic.openfoam_observer.sys.stdout',output),patch('simclinic.openfoam_observer.sys.stderr',errors):
                with self.assertRaises(SystemExit) as raised:observer_main()
            self.assertEqual(raised.exception.code,65)
            metadata=json.loads(next(native.rglob('metadata.json')).read_text())
            gate=json.loads(next(native.rglob('decision.json')).read_text())
            self.assertEqual(metadata['exit_code'],0)
            self.assertEqual(metadata['outcome'],'unknown')
            self.assertEqual(gate['decision'],'stop')
    def fixture(self):
        data=json.loads((ROOT/'fixtures/recovered/record.json').read_text())
        data['session_id']='synthetic-session'
        for event in data['events']:event['session_id']=data['session_id']
        failed=next(e for e in data['events'] if e['id']=='solve')
        failed['run_id']='run-before';failed['input_version']='sha256:input-before'
        return data

    def test_changed_input_and_run_recovery_requires_explicit_session_lineage(self):
        data=self.fixture()
        self.assertEqual(error_continuation(Record(validate(data),'synthetic'))['status'],'pass')
        retry=next(e for e in data['events'] if e['id']=='retry')
        retry.pop('retry_of')
        self.assertEqual(error_continuation(Record(validate(data),'synthetic'))['status'],'finding')

    def test_unrelated_session_cannot_recover_a_failure(self):
        data=self.fixture()
        error=next(e for e in data['events'] if e['id']=='solve')
        # Keep failure in the current run, but give the claimed retry another session.
        error['run_id']=data['run_id'];error['input_version']=data['input_version']
        retry=next(e for e in data['events'] if e['id']=='retry')
        retry['run_id']='different-run';retry['session_id']='unrelated'
        result=error_continuation(Record(data,'synthetic'))
        self.assertEqual(result['status'],'finding')

    def test_same_failure_across_run_ids_is_not_hidden_by_new_uuid(self):
        data=self.fixture()
        error=next(e for e in data['events'] if e['id']=='solve')
        retry=next(e for e in data['events'] if e['id']=='retry')
        retry.update(outcome='fail',exit_code=1,input_version=error['input_version'],
                     input_fingerprint=error['input_fingerprint'],error_category=error['error_category'],arguments=error['arguments'])
        self.assertEqual(retries(Record(validate(data),'synthetic'))['status'],'finding')
        retry['input_version']='sha256:changed'
        self.assertEqual(retries(Record(validate(data),'synthetic'))['status'],'pass')

    def test_mismatched_session_field_rejected(self):
        data=self.fixture();data['events'][0]['session_id']='other'
        with self.assertRaises(RecordError):validate(data)

    def test_no_terminal_event_cannot_be_promoted_to_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder=SessionRecorder(temporary,task_definition('Synthetic task'),{}, {})
            recorder.data['synthetic']=True
            data=recorder.finish(completed=True)
            self.assertEqual(data['collection_status'],'aborted')
            self.assertFalse(any(data['coverage'].values()))
            self.assertFalse(any(e['kind']=='claim' for e in data['events']))

    def test_unknown_assessment_never_creates_success_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder=SessionRecorder(temporary,task_definition('Synthetic task'),{}, {})
            recorder.data['synthetic']=True
            gate=ChannelCompletion(recorder,ROOT/'integration/foam_agent_v210/reference-corpus-v1/reference-case','test-image')
            result=gate({'case_dir':str(Path(temporary)/'missing'),'error_logs':['Synthetic missing execution']})
            self.assertFalse(result['completion_claim']['success'])
            self.assertIsNone(result['acceptance_report']['values']['profile_error'])
            self.assertFalse(any(e['kind']=='claim' for e in recorder.data['events']))
            recorder.observe('end','completion',result)
            recorder.finish()
            self.assertFalse(next(e for e in recorder.data['events'] if e['kind']=='claim')['success'])

    def test_missing_gate_downgrades_decision_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);case=base/'case';run='a'*32;version='sha256:test'
            native=case/'.simclinic/native'/run;native.mkdir(parents=True)
            (native/'execution.json').write_text(json.dumps(dict(exit_code=0,policy_validated=True)))
            (native/'record.json').write_text(json.dumps(dict(artifacts=[],events=[])))
            for i,operation in enumerate(('blockMesh','checkMesh','simpleFoam')):
                folder=native/'command-calls'/str(i);folder.mkdir(parents=True)
                (folder/'metadata.json').write_text(json.dumps(dict(operation=operation,started=i,
                    input_version=version,phase='preflight' if operation=='checkMesh' else 'solve' if operation=='simpleFoam' else 'other',
                    input_fingerprint='test',argv=[],outcome='pass',exit_code=0,elapsed_seconds=0.1)))
            recorder=SessionRecorder(base,task_definition('Synthetic task'),{}, {})
            recorder.data['synthetic']=True
            recorder.capture_attempt(case,dict(run_id=run,input_version=version))
            recorder.observe('end','completion',dict(completion_claim={'success':False,'emitter':'Synthetic test','status':'unknown'}))
            data=recorder.finish()
            self.assertTrue(data['coverage']['tools'])
            self.assertFalse(data['coverage']['decisions'])

    def test_missing_successful_process_metadata_downgrades_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);case=base/'case';run='b'*32;native=case/'.simclinic/native'/run
            native.mkdir(parents=True)
            (native/'execution.json').write_text(json.dumps(dict(exit_code=0,policy_validated=True)))
            (native/'record.json').write_text(json.dumps(dict(artifacts=[],events=[])))
            recorder=SessionRecorder(base,task_definition('Synthetic task'),{}, {})
            recorder.capture_attempt(case,dict(run_id=run,input_version='sha256:test'))
            self.assertFalse(recorder.tools_complete)

    def test_fifth_patch_round_trip(self):
        base=ROOT/'integration/foam_agent_v210/native/full';target=base.parent/'observed'
        patch=ROOT/'patches/foam-agent-v2.1.0/0005-observed-decisions-completion.patch'
        with tempfile.TemporaryDirectory() as temporary:
            shutil.copytree(base,temporary,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
            subprocess.run(['git','apply',str(patch)],cwd=temporary,check=True,capture_output=True)
            for name,sha in json.loads((target/'candidate-manifest.json').read_text())['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(temporary)/name).read_bytes()).hexdigest(),sha)
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=temporary,check=True,capture_output=True)
            for name,sha in json.loads((base/'candidate-manifest.json').read_text())['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(temporary)/name).read_bytes()).hexdigest(),sha)


if __name__=='__main__':unittest.main()
