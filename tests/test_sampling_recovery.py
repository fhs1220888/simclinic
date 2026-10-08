"""Synthetic process/provenance tests; no CFD or model execution."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.domains.openfoam.sampling_gate import GatedChannelCompletion, SamplingError
from simclinic.domains.openfoam.sampling_recovery import BoundedSamplingRecovery
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.checks.behavior import retries
from simclinic.core.record import load_record
from simclinic.result_guard import ResultRejected
from tests import test_sampling_gate as gate_fixtures


class Candidate(BoundedSamplingRecovery, GatedChannelCompletion):
    pass


class SamplingRecoveryTests(unittest.TestCase):
    def execute(self, root, mode, stale=False):
        fixture=gate_fixtures.SamplingGateTests()
        base,work,context=fixture.setup_case(root)
        policy=Candidate(base.recorder,base.reference,base.image)
        retry=Path(root)/'fresh-retry';retry.mkdir()
        calls=[]
        @contextmanager
        def workspace(*args):
            yield retry
        def run(argv,**kwargs):
            if argv[1]=='rm':return subprocess.CompletedProcess(argv,0,b'',b'')
            mount=next(a for a in argv if a.startswith('type=bind,src='))
            actual_work=Path(mount.split('src=',1)[1].split(',dst=',1)[0]);calls.append(actual_work)
            if mode=='normal' or (mode=='transient' and len(calls)==2):
                fixture.outputs(actual_work)
            if mode=='fatal':
                return subprocess.CompletedProcess(argv,0,b'FOAM FATAL IO ERROR\nEnd\n',b'')
            if mode=='timeout':raise subprocess.TimeoutExpired(argv,120,output=b'partial')
            return subprocess.CompletedProcess(argv,0,b'End\n',b'')
        marker=dict(context,source_case=str(work),input_files={})
        error=None
        with patch('simclinic.domains.openfoam.sampling_gate.verify_workspace',return_value=context), \
             patch('simclinic.domains.openfoam.sampling_gate.subprocess.run',side_effect=run), \
             patch('simclinic.domains.openfoam.sampling_recovery.verify_workspace',return_value=marker), \
             patch('simclinic.domains.openfoam.sampling_recovery.verified_workspace',workspace), \
             patch('simclinic.domains.openfoam.sampling_recovery.current_record',side_effect=ResultRejected('stale_input','Synthetic identity change') if stale else None):
            try:policy.sample(work,context)
            except (SamplingError,ResultRejected) as exc:error=exc
        record=load_record(policy.recorder.directory/'record.json',policy.recorder.directory)
        return policy,record,calls,error

    def test_transient_recovery_retains_first_failure_and_links_retry(self):
        with tempfile.TemporaryDirectory() as root:
            policy,record,calls,error=self.execute(root,'transient')
            self.assertIsNone(error);self.assertEqual(len(calls),2);self.assertNotEqual(calls[0],calls[1])
            events=[e for e in record.data['events'] if e.get('operation')=='sample']
            self.assertEqual(events[1]['retry_of'],events[0]['id'])
            self.assertEqual(events[1]['arguments'],events[0]['arguments'])
            self.assertEqual([e['outcome'] for e in events],['fail','pass'])
            self.assertTrue(any(e.get('decision_type')=='bounded_postprocess_recovery' for e in record.data['events']))
            self.assertEqual(policy.recorder.directory,(Path(root)/'session').resolve())
            for a in record.data['artifacts']:
                self.assertEqual(hashlib.sha256((record.case_dir/a['path']).read_bytes()).hexdigest(),a['sha256'])
            result=diagnose_sampling(record)
            self.assertEqual([r['status'] for r in result['runs']],['insufficient_evidence','pass'])
            self.assertEqual(retries(record)['status'],'pass')

    def test_persistent_loss_stops_after_two_and_does_not_disguise_unchanged_retry(self):
        with tempfile.TemporaryDirectory() as root:
            policy,record,calls,error=self.execute(root,'persistent')
            self.assertEqual(error.code,'sampling_missing_output');self.assertEqual(len(calls),2)
            self.assertEqual(policy.recovery_attempts,1)
            self.assertEqual(retries(record)['status'],'finding')
            self.assertEqual(diagnose_sampling(record)['summary']['insufficient_evidence'],2)

    def test_normal_sampler_never_retries(self):
        with tempfile.TemporaryDirectory() as root:
            policy,record,calls,error=self.execute(root,'normal')
            self.assertIsNone(error);self.assertEqual(len(calls),1)
            self.assertFalse(hasattr(policy,'recovery_attempts'))

    def test_fatal_or_timeout_not_eligible_for_recovery(self):
        for mode,code in (('fatal','sampling_fatal_log'),('timeout','sampling_timeout')):
            with tempfile.TemporaryDirectory() as root:
                policy,record,calls,error=self.execute(root,mode)
                self.assertEqual(error.code,code);self.assertEqual(len(calls),1)
                self.assertFalse(hasattr(policy,'recovery_attempts'))

    def test_stale_identity_prevents_retry(self):
        with tempfile.TemporaryDirectory() as root:
            policy,record,calls,error=self.execute(root,'transient',stale=True)
            self.assertEqual(error.code,'stale_input');self.assertEqual(len(calls),1)
            self.assertFalse(hasattr(policy,'recovery_attempts'))

    def test_declared_archive_root_cannot_redirect_to_other_or_unsafe_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            policy,record,calls,error=self.execute(root,'transient')
            event=[e for e in record.data['events'] if e.get('operation')=='sample'][-1]
            for bad in ('../outside','observations/sampling','/absolute',42):
                event['output_archive_root']=bad
                result=diagnose_sampling(record)['runs'][-1]
                self.assertEqual(result['status'],'insufficient_evidence')
                self.assertTrue(any(e['kind']=='output_archive_root' for e in result['unavailable_evidence']))


if __name__=='__main__':
    unittest.main()
