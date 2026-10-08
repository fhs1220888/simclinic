"""Response contract tests; synthetic text does not establish CFD correctness."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from simclinic.input_content import decode_foamfile_response, InputContentError
from scripts.report_heldout import attempt_metrics
from simclinic.evaluation.content_study import TASKS as NEW_TASKS
from simclinic.evaluation.heldout import TASKS as OLD_TASKS, DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
BODY = '/* Test-only dictionary. */\nFoamFile\n{ version 2.0; format ascii; class dictionary; object controlDict; }\napplication simpleFoam;\n'


def decode(text):
    return decode_foamfile_response(text, file_name='controlDict', folder_name='system')


class InputContentTests(unittest.TestCase):
    def test_no_execution_unknown_status_and_recovery_are_distinct(self):
        missing = attempt_metrics({'attempts':[]},{'type':'InputContentError'})
        self.assertTrue(missing['input_content_rejected'])
        self.assertIsNone(missing['first_execution_failed'])
        self.assertEqual(missing['execution_attempts'],0)
        recovered = attempt_metrics({'attempts':[{'execution':{'exit_code':1}}, {'execution':{'exit_code':0}}]},None)
        self.assertTrue(recovered['first_execution_failed'])
        self.assertEqual(recovered['failed_execution_attempts'],1)
        unknown = attempt_metrics({'attempts':[{'execution':{'exit_code':None}}]},None)
        self.assertIsNone(unknown['first_execution_failed'])
        self.assertIsNone(unknown['failed_execution_attempts'])

    def test_new_study_parameters_do_not_reuse_previous_tasks(self):
        old = {(s['mean_velocity'],s['nx'],s['ny']) for s in OLD_TASKS+[DEVELOPMENT]}
        new = {(s['mean_velocity'],s['nx'],s['ny']) for s in NEW_TASKS}
        self.assertEqual(len(new),3)
        self.assertFalse(old & new)

    def test_raw_dictionary_and_quoted_escapes_preserved_exactly(self):
        body = BODY+'description "literal \\n and https://example.invalid"; // preserve comment\n'
        self.assertEqual(decode(body),body)

    def test_json_once_decodes_envelope_without_changing_parameters(self):
        self.assertEqual(decode(json.dumps(dict(file_name='controlDict',folder_name='system',content=BODY))),BODY)

    def test_complete_fences_and_quoted_header_object(self):
        body = BODY.replace('object controlDict;', 'object "controlDict";')
        self.assertEqual(decode('```foam\n'+body+'```'),body.rstrip('\n'))
        envelope = json.dumps(dict(file_name='controlDict',folder_name='system',content=body))
        self.assertEqual(decode('```json\n'+envelope+'\n```'),body)

    def test_identity_mismatch_and_extra_fields_rejected(self):
        for update in ({'file_name':'fvSolution'},{'folder_name':'constant'},{'extra':'data'}, {'content':None}):
            with self.subTest(update=update), self.assertRaises(InputContentError):
                decode(json.dumps(dict(file_name='controlDict',folder_name='system',content=BODY,**{}) | update))

    def test_duplicate_or_malformed_json_rejected(self):
        envelope = json.dumps(dict(file_name='controlDict',folder_name='system',content=BODY))
        for text in ('{"content":"one","content":"two"}', envelope[:-1], envelope+' extra'):
            with self.subTest(text=text[:35]), self.assertRaises(InputContentError):decode(text)

    def test_nested_list_double_encoded_and_literal_newline_rejected(self):
        envelope = dict(file_name='controlDict',folder_name='system',content=BODY)
        for text in (json.dumps([envelope]),json.dumps(json.dumps(envelope)),
                     json.dumps(dict(envelope,content=json.dumps(envelope))),
                     json.dumps(dict(envelope,content=BODY.replace('\n','\\n'))),
                     BODY+'endTime 2000;\\n'):
            with self.subTest(text=text[:35]), self.assertRaises(InputContentError):decode(text)

    def test_prose_multiple_fences_wrong_header_and_empty_rejected(self):
        for text in ('Here is the dictionary:\n'+BODY,'```foam\n'+BODY+'```\ntrailing prose',
                     '```foam\n'+BODY+'```\n```foam\n'+BODY+'```',
                     BODY.replace('object controlDict','object U'),'','no dictionary',BODY+'\x00'):
            with self.subTest(text=text[:35]), self.assertRaises(InputContentError):decode(text)

    @unittest.skipUnless((ROOT/'real-runs/heldout-field-selection-20261004/runs/H2-r3-candidate/record.json').is_file(),
                         'Optional historical response archive; see docs/archive-access.md')
    def test_actual_archived_response_is_exact_failed_file_and_decodes_to_repaired_body(self):
        run = ROOT/'real-runs/heldout-field-selection-20261004/runs/H2-r3-candidate'
        response = json.loads((run/'model-calls/call-008/response.json').read_text())['text']
        attempts = json.loads((run/'record.json').read_text())['attempts']
        failed = run/'attempts'/attempts[0]['run_id']/'native/input-snapshot/system/controlDict'
        repaired = run/'attempts'/attempts[1]['run_id']/'native/input-snapshot/system/controlDict'
        self.assertEqual(response.encode(),failed.read_bytes())
        self.assertEqual(decode(response),repaired.read_text())

    def test_sixth_patch_round_trip_and_prior_snapshot_preserved(self):
        base = ROOT/'integration/foam_agent_v210/native/observed'
        candidate = base.parent/'content'
        patch = ROOT/'patches/foam-agent-v2.1.0/0006-file-content-contract.patch'
        with tempfile.TemporaryDirectory() as temporary:
            shutil.copytree(base,temporary,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
            subprocess.run(['git','apply',str(patch)],cwd=temporary,check=True,capture_output=True)
            for name, sha in json.loads((candidate/'candidate-manifest.json').read_text())['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(temporary)/name).read_bytes()).hexdigest(),sha)
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=temporary,check=True,capture_output=True)
            for name, sha in json.loads((base/'candidate-manifest.json').read_text())['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(temporary)/name).read_bytes()).hexdigest(),sha)


if __name__=='__main__':unittest.main()
