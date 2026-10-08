"""The holdout catalog must see hidden inputs and leave ambiguity unknown."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOCATION = ROOT / 'scripts/inventory_couette_tasks.py'
SPEC = importlib.util.spec_from_file_location('couette_inventory', LOCATION)
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)

MESH = '''FoamFile {version 2.0; format ascii; class dictionary; object blockMeshDict;}
blocks (hex (0 1 2 3 4 5 6 7) (48 23 1) simpleGrading (1 1 1));
boundary (west {type patch;} east {type patch;} south {type wall;}
          north {type wall;} frontAndBack {type empty;});
'''
VELOCITY = '''FoamFile {version 2.0; format ascii; class volVectorField; object U;}
internalField uniform (0 0 0);
boundaryField {west {type fixedValue; value nonuniform List<vector> 1 ((.01 0 0));}
east {type zeroGradient;} south {type fixedValue; value uniform (0 0 0);}
north {type fixedValue; value uniform (+2.2e-2 0 0);} frontAndBack {type empty;}}
'''


class CouetteInventoryTests(unittest.TestCase):
    def root(self, temporary):
        root = Path(temporary)
        (root / 'real-runs').mkdir()
        return root

    def put(self, root, name, payload):
        path = root / 'real-runs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        return path

    def pair(self, root, name='hidden-negative-input'):
        mesh = self.put(root, name + '/system/blockMeshDict', MESH)
        velocity = self.put(root, name + '/0/U', VELOCITY)
        return mesh, velocity

    def test_recursive_frozen_spec_and_spec_absent_input_are_both_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            spec = dict(kind='couette', id='C3', wall_speed=.022, nx=46, ny=23)
            freeze = self.put(root, 'batch/freeze.json', json.dumps(dict(entries=[dict(spec=spec)])))
            mesh, velocity = self.pair(root)
            report = catalog.inventory(root)
            self.assertTrue(report['scan_complete'])
            self.assertEqual([(x['wall_speed'], x['nx'], x['ny']) for x in report['combinations']],
                             [(.022, 46, 23), (.022, 48, 23)])
            self.assertEqual(report['combinations'][0]['sources'][0]['pointer'], '/entries/0/spec')
            self.assertEqual({x['kind'] for x in report['combinations'][1]['sources']},
                             {'input_mesh', 'input_velocity'})
            for path in (freeze, mesh, velocity):
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertIn(dict(path=path.relative_to(root).as_posix(), sha256=expected), report['scanned_files'])
            self.assertEqual(report['new_model_calls'], 0)
            self.assertEqual(report['new_simulations'], 0)

    def test_complete_pair_with_expression_or_duplicate_is_unknown(self):
        for altered in (VELOCITY.replace('+2.2e-2', '#calc "0.024"'),
                        VELOCITY.replace('internalField uniform (0 0 0);',
                                         'internalField uniform (0 0 0); internalField uniform (0 0 0);'),
                        VELOCITY.replace('+2.2e-2', 'wall_speed')):
            with self.subTest(altered=altered), tempfile.TemporaryDirectory() as temporary:
                root = self.root(temporary)
                mesh, velocity = self.pair(root)
                velocity.write_text(altered)
                report = catalog.inventory(root)
                self.assertFalse(report['scan_complete'])
                self.assertEqual(len(report['errors']), 1)
                self.assertEqual(report['combinations'], [])

    def test_duplicate_named_mesh_patch_and_expression_counts_are_unknown(self):
        for altered in (MESH.replace('(48 23 1)', '(nx 23 1)'),
                        MESH.replace('west {type patch;}', 'west {type patch;} west {type patch;}')):
            with self.subTest(altered=altered), tempfile.TemporaryDirectory() as temporary:
                root = self.root(temporary)
                mesh, velocity = self.pair(root)
                mesh.write_text(altered)
                report = catalog.inventory(root)
                self.assertFalse(report['scan_complete'])
                self.assertEqual(report['combinations'], [])

    def test_ambiguous_json_is_not_accepted_as_known_parameters(self):
        payloads = ['{"kind":"couette","wall_speed":.02}',
                    '{"kind":"couette","wall_speed":0.02,"wall_speed":0.03,"nx":40,"ny":20}',
                    '{"kind":"couette","wall_speed":NaN,"nx":40,"ny":20}',
                    '{"kind":"couette","wall_speed":0.02,"nx":true,"ny":20}']
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                root = self.root(temporary)
                self.put(root, 'batch/spec.json', payload)
                report = catalog.inventory(root)
                self.assertFalse(report['scan_complete'])
                self.assertEqual(len(report['errors']), 1)

    def test_registered_single_file_fixture_keeps_partial_or_unknown_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            positive = self.put(root, 'batch/offline-input-matrix/positive/system/blockMeshDict', MESH)
            directive = self.put(root, 'batch/offline-input-matrix/directive/system/blockMeshDict', '#include "other"\n' + MESH)
            velocity = self.put(root, 'batch/offline-input-matrix/velocity/0/U', VELOCITY)
            matrix = positive.parents[2]
            registered = {p.relative_to(matrix).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (positive, directive, velocity)}
            self.put(root, 'batch/offline-input-matrix-manifest.json', json.dumps(dict(files=registered)))
            report = catalog.inventory(root)
            self.assertTrue(report['scan_complete'])
            self.assertEqual(report['combinations'], [])
            self.assertEqual(len(report['partial_inputs']), 3)
            known = next(x for x in report['partial_inputs'] if x['path'].endswith('positive/system/blockMeshDict'))
            self.assertEqual((known['nx'], known['ny'], known['wall_speed']), (48, 23, None))
            unknown = next(x for x in report['partial_inputs'] if x['path'].endswith('directive/system/blockMeshDict'))
            self.assertEqual(unknown['parameter_status'], 'unresolved')
            self.assertFalse(unknown['complete_task'])
            self.assertEqual(len(report['exclusions']), 3)
            positive.write_text(MESH + '// bytes changed\n')
            changed = catalog.inventory(root)
            self.assertFalse(changed['scan_complete'])

    def test_missing_input_companion_and_symlink_make_catalog_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            mesh = self.put(root, 'case/system/blockMeshDict', MESH)
            report = catalog.inventory(root)
            self.assertFalse(report['scan_complete'])
            self.assertIn('missing_velocity', report['errors'][0]['reason'])
            mesh.unlink()
            outside = Path(temporary) / 'outside'
            outside.write_text(MESH)
            mesh.symlink_to(outside)
            report = catalog.inventory(root)
            self.assertFalse(report['scan_complete'])
            self.assertIn('symlink_source', report['errors'][0]['reason'])

    def test_linked_directory_does_not_hide_a_task_behind_complete_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            outside = root / 'external-tasks'
            outside.mkdir()
            (outside / 'spec.json').write_text(json.dumps(dict(kind='couette', wall_speed=.024, nx=58, ny=29)))
            (root / 'real-runs/linked-tasks').symlink_to(outside, target_is_directory=True)
            report = catalog.inventory(root)
            self.assertFalse(report['scan_complete'])
            self.assertEqual(report['errors'][0]['reason'], 'symlink_directory_not_traversed')
            self.assertEqual(report['combinations'], [])

    def test_cli_refuses_history_frozen_output_and_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.root(temporary)
            self.pair(root)
            old = root / 'existing.json'
            old.write_bytes(b'user content')
            frozen = root / 'frozen'
            frozen.mkdir()
            (frozen / 'freeze.sha256').write_bytes(b'frozen')
            for target in (old, root / 'real-runs/new.json', frozen / 'new.json'):
                with self.subTest(target=target):
                    self.assertEqual(catalog.main(['--root', str(root), '--out', str(target)]), 2)
            self.assertEqual(old.read_bytes(), b'user content')
            out = root / 'new-output/report.json'
            self.assertEqual(catalog.main(['--root', str(root), '--out', str(out)]), 0)
            self.assertTrue(json.loads(out.read_text())['scan_complete'])


if __name__ == '__main__':
    unittest.main()
