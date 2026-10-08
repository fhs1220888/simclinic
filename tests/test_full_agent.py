"""Backend boundary tests use labelled test doubles, never real-run evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from simclinic.agent_paths import case_input_path
from simclinic.backends.codex_cli import CodexCLI, strict_schema
from simclinic.foam_execution import validate_allrun
from simclinic.retrieval import LexicalReferences
from simclinic.rendering_request import choose_field

ROOT = Path(__file__).resolve().parents[1]


class FullAgentTests(unittest.TestCase):
    def test_render_request_overrides_pressure_acceptance(self):
        self.assertEqual(choose_field('p is fixed at outlet; pressure-gradient error <3%. Explicitly render the velocity U field to a PNG.'),'U')
        self.assertEqual(choose_field('Velocity profile error <2%. Render the pressure field.'),'p')

    def test_ambiguous_render_request_rejected(self):
        with self.assertRaises(ValueError):choose_field('Render U. Plot pressure.')
        self.assertEqual(choose_field('Render the temperature field.'),'T')
    def test_model_paths_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)/'case'
            for folder,name in [('..','file'),('system','../file'),('/tmp','file'),('0','a/b'),('0','a\\b')]:
                with self.assertRaises(ValueError):case_input_path(case,folder,name)
            self.assertFalse(case.exists())
            self.assertEqual(case_input_path(case,'0','U'),case.resolve()/'0/U')

    def test_model_paths_reject_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)/'case';case.mkdir()
            outside=Path(tmp)/'outside';outside.mkdir()
            (case/'system').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):case_input_path(case,'system','controlDict')

    def test_allrun_policy_makes_command_scope_explicit(self):
        script='#!/bin/bash\nset -eu\nblockMesh > log.blockMesh 2>&1\ncheckMesh > log.checkMesh 2>&1\nsimpleFoam > log.simpleFoam 2>&1\n'
        validate_allrun(script)
        for bad in (script+'touch .simclinic/native/current.json\n',script.replace('checkMesh > log.checkMesh 2>&1','true'),script.replace('set -eu','set +e'),script.replace('simpleFoam','/opt/openfoam10/bin/simpleFoam')):
            with self.assertRaises(ValueError):validate_allrun(bad)

    def test_nested_strict_schema(self):
        schema={'type':'object','properties':{'x':{'$ref':'#/$defs/Child'}},'$defs':{'Child':{'type':'object','properties':{'value':{'type':'string','default':'x'}}}}}
        converted=strict_schema(schema)
        self.assertFalse(converted['additionalProperties'])
        self.assertEqual(converted['$defs']['Child']['required'],['value'])
        self.assertNotIn('default',converted['$defs']['Child']['properties']['value'])
        self.assertIn('default',schema['$defs']['Child']['properties']['value'])

    def test_lexical_adapter_reports_its_method_and_no_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'corpus.json'
            path.write_text(json.dumps({'collections':{'test':[{'id':'c1','index':'laminar simpleFoam','full_content':'example'}]}}))
            adapter=LexicalReferences(path)
            hit=adapter.search('test','simpleFoam')[0]
            self.assertEqual(hit['retrieval_method'],'lexical-overlap-v1')
            self.assertEqual(hit['corpus_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(ValueError):adapter.search('test','combustion')
            with self.assertRaises(ValueError):adapter.search('missing','simpleFoam')

    def test_backend_filters_reasoning_and_records_actual_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            class TestProcess:
                returncode=0
                def communicate(self,prompt,timeout):
                    return '\n'.join(json.dumps(e) for e in [
                        {'type':'item.completed','item':{'type':'reasoning','text':'TEST HIDDEN REASONING'}},
                        {'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':4}}]),''
            def create(argv,**kwargs):
                self.assertIn('--ignore-user-config',argv)
                self.assertIn('--ephemeral',argv)
                self.assertEqual(kwargs['cwd'],Path(tmp).resolve()/'call-001')
                (kwargs['cwd']/'response.json').write_text('{"text":"test answer"}')
                return TestProcess()
            with patch('simclinic.backends.codex_cli.subprocess.check_output',return_value='test-cli'),patch('simclinic.backends.codex_cli.subprocess.Popen',side_effect=create):
                backend=CodexCLI(tmp,'test-model',max_calls=1)
                self.assertEqual(backend.invoke('test prompt'),'test answer')
                self.assertEqual(backend.get_statistics()['total_tokens'],24)
                self.assertIsNone(backend.get_statistics()['model_cost'])
                with self.assertRaises(RuntimeError):backend.invoke('another')
            for path in Path(tmp).rglob('*.json'):
                self.assertNotIn('TEST HIDDEN REASONING',path.read_text())

    def test_backend_unexpected_tool_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            class TestProcess:
                returncode=0
                def communicate(self,prompt,timeout):
                    return json.dumps({'type':'item.completed','item':{'type':'command_execution'}}),''
            with patch('simclinic.backends.codex_cli.subprocess.check_output',return_value='test-cli'),patch('simclinic.backends.codex_cli.subprocess.Popen',return_value=TestProcess()):
                backend=CodexCLI(tmp,'test-model')
                with self.assertRaisesRegex(RuntimeError,'Unexpected'):backend.invoke('test')
                self.assertEqual(backend.calls[0]['outcome'],'fail')

    def test_fourth_patch_round_trip(self):
        base=ROOT/'integration/foam_agent_v210/native/patched'
        full=base.parent/'full'
        diff=ROOT/'patches/foam-agent-v2.1.0/0004-full-agent-backends.patch'
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(base,tmp,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
            subprocess.run(['git','apply',str(diff)],cwd=tmp,check=True,capture_output=True)
            manifest=json.loads((full/'candidate-manifest.json').read_text())
            for name,sha in manifest['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(tmp)/name).read_bytes()).hexdigest(),sha,name)
            subprocess.run(['git','apply','--reverse',str(diff)],cwd=tmp,check=True,capture_output=True)
            for name,sha in json.loads((base/'candidate-manifest.json').read_text())['source_files'].items():
                self.assertEqual(hashlib.sha256((Path(tmp)/name).read_bytes()).hexdigest(),sha,name)


if __name__=='__main__':unittest.main()
