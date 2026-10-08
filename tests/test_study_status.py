"""Synthetic status fixtures; no models or solvers are executed."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout,redirect_stderr

from simclinic.cli import main
from simclinic.evaluation.study_status import study_status


class StudyStatusTests(unittest.TestCase):
    def test_finished_unknown_total_keeps_known_call_lower_bound(self):
        with tempfile.TemporaryDirectory() as t:
            b,f,sha=self.setup_batch(t)
            entry=dict(f['schedule'][0],status='collection_or_setup_failure',directory=str(b/'runs'/f['schedule'][0]['id']),model_tokens=None)
            folder=Path(entry['directory'])/'model-calls'
            for n,value in ((1,100),(2,None)):
                p=folder/f'call-{n:03d}'/'metadata.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'total_tokens':value}))
            self.progress(b,sha,[entry],status='usage_unknown')
            s=study_status(b)
            self.assertEqual(s['usage']['completed_model_tokens'],0)
            self.assertEqual(s['usage']['finished_partial_known_model_tokens'],100)
            self.assertEqual(s['usage']['observed_model_tokens_lower_bound'],100)
            self.assertIsNone(s['usage']['observed_model_tokens'])
            self.assertIsNone(s['usage']['remaining_from_observed_usage'])
    def setup_batch(self,directory):
        b=Path(directory)/'batch';b.mkdir()
        for folder in ('common-runtime','corpus','variants/baseline','variants/candidate'):(b/folder).mkdir(parents=True)
        f=dict(experiment='Synthetic status-only test',common_runtime={},corpus={},tasks={},
            variants={'baseline':{},'candidate':{}},batch_model_token_limit=1000,
            schedule=[dict(id='I1-r1-'+arm,arm=arm,task='I1',repeat=1) for arm in ('baseline','candidate')])
        (b/'freeze.json').write_text(json.dumps(f));sha=hashlib.sha256((b/'freeze.json').read_bytes()).hexdigest()
        (b/'freeze.sha256').write_text(sha)
        return b,f,sha

    def progress(self,b,sha,runs,development=None,status='running',tokens=0):
        (b/'progress.json').write_text(json.dumps(dict(status=status,freeze_sha256=sha,
            development=development or [],runs=runs,consumed_model_tokens=tokens)))

    def test_frozen_not_started_and_query_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as t:
            b,_,_=self.setup_batch(t);before={p:p.read_bytes() for p in b.rglob('*') if p.is_file()}
            s=study_status(b)
            self.assertEqual(s['status'],'frozen_not_started');self.assertEqual(s['held']['finished'],0)
            self.assertEqual(s['held']['not_started'],2);self.assertEqual(s['usage']['observed_model_tokens'],0)
            self.assertEqual(before,{p:p.read_bytes() for p in b.rglob('*') if p.is_file()})

    def test_active_unknown_usage_is_not_zero_or_double_counted(self):
        with tempfile.TemporaryDirectory() as t:
            b,f,sha=self.setup_batch(t);runs=[]
            first=dict(f['schedule'][0],status='finished',quality_met=False,evidence_complete=True,
                       model_tokens=100,directory=str(b/'runs'/f['schedule'][0]['id']))
            second=dict(f['schedule'][1],status='running',directory=str(b/'runs'/f['schedule'][1]['id']))
            folder=Path(second['directory'])/'model-calls'
            for n,value in ((1,50),(2,None)):
                p=folder/f'call-{n:03d}'/'metadata.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'total_tokens':value}))
            self.progress(b,sha,[first,second],tokens=100)
            s=study_status(b)
            self.assertEqual(s['usage']['observed_model_tokens_lower_bound'],150)
            self.assertIsNone(s['usage']['observed_model_tokens']);self.assertIsNone(s['usage']['remaining_from_observed_usage'])
            self.assertEqual(s['held']['quality_failed'],1);self.assertEqual(s['held']['active'],1)

    def test_missing_finished_usage_and_quality_remain_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            b,f,sha=self.setup_batch(t)
            entry=dict(f['schedule'][0],status='collection_or_setup_failure',directory=str(b/'runs'/f['schedule'][0]['id']))
            self.progress(b,sha,[entry],status='usage_unknown')
            s=study_status(b)
            self.assertEqual(s['held']['quality_unknown'],1);self.assertEqual(s['held']['quality_failed'],0)
            self.assertIsNone(s['usage']['observed_model_tokens'])

    def test_foreign_run_directory_and_changed_schedule_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            b,f,sha=self.setup_batch(t)
            entry=dict(f['schedule'][0],status='running',directory=t)
            self.progress(b,sha,[entry])
            with self.assertRaises(ValueError):study_status(b)
            entry['directory']=str(b/'runs'/entry['id']);entry['task']='other'
            self.progress(b,sha,[entry])
            with self.assertRaises(ValueError):study_status(b)

    def test_modified_freeze_or_code_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            b,_,_=self.setup_batch(t);(b/'common-runtime/extra.py').write_text('Test-only extra')
            with self.assertRaises(ValueError):study_status(b)
            (b/'common-runtime/extra.py').unlink();(b/'freeze.json').write_text('{}')
            with self.assertRaises(ValueError):study_status(b)

    def test_live_partial_metadata_and_completed_usage_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            b,f,sha=self.setup_batch(t);entry=dict(f['schedule'][0],status='running',directory=str(b/'runs'/f['schedule'][0]['id']))
            path=Path(entry['directory'])/'model-calls/call-001/metadata.json';path.parent.mkdir(parents=True);path.write_text('{')
            self.progress(b,sha,[entry]);self.assertIsNone(study_status(b)['usage']['observed_model_tokens'])
            self.progress(b,sha,[entry],tokens=42)
            with self.assertRaises(ValueError):study_status(b)

    def test_cli_exports_outside_batch_and_refuses_source_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            b,_,_=self.setup_batch(t);out=Path(t)/'status.json'
            with redirect_stdout(io.StringIO()):self.assertEqual(main(['study-status',str(b),'--json','--out',str(out)]),0)
            self.assertTrue(json.loads(out.read_text())['read_only'])
            with redirect_stderr(io.StringIO()):self.assertEqual(main(['study-status',str(b),'--out',str(b/'freeze.json')]),2)


if __name__=='__main__':unittest.main()
