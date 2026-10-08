import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import patch

from simclinic.foam_runtime import begin_run, finish_run, current_record, verified_workspace, verify_workspace, record_image
from simclinic.result_guard import ResultRejected


class NativeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='simclinic-native-test-')
        self.addCleanup(self.temp.cleanup)
        self.case=Path(self.temp.name)/'case'
        for name in ('0','constant','system'):
            (self.case/name).mkdir(parents=True)
        (self.case/'0/U').write_text('synthetic unit-test U input')
        (self.case/'0/p').write_text('synthetic unit-test p input')
        (self.case/'Allrun').write_text('exit 0\n')

    def generated(self, context):
        (self.case/'1').mkdir()
        (self.case/'1/U').write_text('synthetic unit-test output U')
        (self.case/'1/p').write_text('synthetic unit-test output p')
        (self.case/'constant/polyMesh').mkdir(exist_ok=True)
        (self.case/'constant/polyMesh/points').write_text('synthetic unit-test mesh')
        self.assertEqual(finish_run(self.case,context,[]),[])

    def context(self):
        context=begin_run(self.case)
        self.generated(context)
        return context

    def rejects(self, code, callable):
        with self.assertRaises(ResultRejected) as raised:
            callable()
        self.assertEqual(raised.exception.code,code)

    def test_cleanup_does_not_adopt_preexisting_time_results(self):
        (self.case/'999').mkdir()
        (self.case/'999/U').write_text('old result')
        context=begin_run(self.case)
        self.assertFalse((self.case/'999').exists())
        errors=finish_run(self.case,context,[])
        self.assertTrue(errors)
        self.rejects('incomplete_run',lambda:current_record(self.case,context))

    def test_fresh_results_copied_as_verified_bytes(self):
        context=self.context()
        with verified_workspace(self.case,context) as work:
            self.assertNotEqual(work,self.case)
            self.assertEqual((work/'1/U').read_bytes(),(self.case/'1/U').read_bytes())
            self.assertTrue((work/'case.foam').exists())
            self.assertEqual(verify_workspace(work)['run_id'],context['run_id'])

    def test_wrong_run_and_input_block_before_solver_file_read(self):
        context=self.context()
        for expected, code in ((dict(context,run_id='0'*32),'stale_run'),(dict(context,input_version='other'),'stale_input')):
            with patch.object(Path,'read_bytes',side_effect=AssertionError('must reject before field consumption')):
                self.rejects(code,lambda:current_record(self.case,expected))

    def test_missing_unknown_and_failed_context_rejected(self):
        for value in (None,{},dict(run_id='../bad',input_version='anything')):
            self.rejects('unknown_expected_identity',lambda:current_record(self.case,value))
        context=begin_run(self.case)
        self.rejects('incomplete_run',lambda:current_record(self.case,context))
        finish_run(self.case,context,[{'exit_code':7}])
        self.rejects('incomplete_run',lambda:current_record(self.case,context))

    def test_actual_input_change_invalidates_context(self):
        context=self.context()
        (self.case/'0/U').write_text('changed inlet')
        self.rejects('stale_input',lambda:current_record(self.case,context))

    def test_mesh_generation_is_output_but_input_changes_during_run_fail(self):
        context=begin_run(self.case)
        (self.case/'0/U').write_text('changed during solve')
        self.assertTrue(finish_run(self.case,context,[]))
        self.rejects('incomplete_run',lambda:current_record(self.case,context))

    def test_changed_field_hash_is_rejected(self):
        context=self.context()
        (self.case/'1/U').write_text('changed output')
        with self.assertRaises(ResultRejected) as raised:
            with verified_workspace(self.case,context):
                self.fail('must not stage changed field')
        self.assertEqual(raised.exception.code,'digest_mismatch')

    def test_second_run_replaces_current_context(self):
        first=self.context()
        second=begin_run(self.case)
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(first['input_version'],second['input_version'])
        self.rejects('stale_run',lambda:current_record(self.case,first))

    def test_untracked_numeric_directory_rejected(self):
        context=self.context()
        (self.case/'500').mkdir()
        self.rejects('untracked_output',lambda:current_record(self.case,context))

    def test_snapshot_mutation_and_source_change_during_render_rejected(self):
        context=self.context()
        with self.assertRaises(ResultRejected) as raised:
            with verified_workspace(self.case,context) as work:
                (work/'1/U').write_text('script modified field')
        self.assertEqual(raised.exception.code,'snapshot_changed')
        with self.assertRaises(ResultRejected) as raised:
            with verified_workspace(self.case,context) as work:
                (self.case/'0/U').write_text('input changed while rendering')
        self.assertEqual(raised.exception.code,'stale_input')

    def test_source_symlink_cannot_be_read_or_staged(self):
        context=self.context()
        field=self.case/'1/U'
        field.unlink()
        field.symlink_to(self.case/'0/U')
        with self.assertRaises(ResultRejected) as raised:
            with verified_workspace(self.case,context):
                self.fail('must reject symlink')
        self.assertEqual(raised.exception.code,'unsafe_path')

    def test_output_provenance_and_png_header_check(self):
        context=self.context()
        with verified_workspace(self.case,context) as work:
            image=work/'image.png'
            image.write_bytes(b'invalid')
            self.rejects('invalid_image',lambda:record_image(work,image))
            image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic unit test bytes')
            result=record_image(work,image)
            self.assertEqual(result['run_id'],context['run_id'])
            self.assertEqual(result['sha256'],hashlib.sha256(image.read_bytes()).hexdigest())
        self.rejects('unverified_workspace',lambda:verify_workspace(self.case))


class NativePatchTests(unittest.TestCase):
    def test_native_patch_applies_and_reverses_complete_package(self):
        root=Path(__file__).resolve().parents[1]
        base=root/'integration/foam_agent_v210/native/upstream'
        candidate=base.parent/'patched'
        names=['0001-execution-status.patch','0002-verified-result-interface.patch','0003-native-postprocessing.patch']
        with tempfile.TemporaryDirectory(prefix='simclinic-native-patch-') as temporary:
            target=Path(temporary)
            shutil.copytree(base,target,dirs_exist_ok=True)
            for name in names:
                patch_file=root/'patches/foam-agent-v2.1.0'/name
                subprocess.run(['git','apply','--check',str(patch_file)],cwd=target,check=True,capture_output=True)
                subprocess.run(['git','apply',str(patch_file)],cwd=target,check=True,capture_output=True)
            manifest=json.loads((candidate/'candidate-manifest.json').read_text())
            for path,sha in manifest['source_files'].items():
                self.assertEqual(hashlib.sha256((target/path).read_bytes()).hexdigest(),sha,path)
            for name in reversed(names):
                subprocess.run(['git','apply','--reverse',str(root/'patches/foam-agent-v2.1.0'/name)],cwd=target,check=True,capture_output=True)
            baseline=json.loads((base/'source-manifest.json').read_text())
            for path,sha in baseline['sha256'].items():
                self.assertEqual(hashlib.sha256((target/path).read_bytes()).hexdigest(),sha,path)


if __name__=='__main__':
    unittest.main()
