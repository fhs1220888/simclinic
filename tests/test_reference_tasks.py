"""Analytic and data-integrity tests only, no CFD/model execution."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from simclinic.domains.openfoam.reference_tasks import (
    DEVELOPMENT_TASKS, analytic_velocity, create_reference, task,
    pressure_gradient_scale, reference_measurements, validate_spec, couette_inlet_order)
from simclinic.domains.openfoam.reference_tasks import field_values, stationary_fields

ROOT = Path(__file__).resolve().parents[1]


class ReferenceTaskTests(unittest.TestCase):
    def samples(self, root, spec, gradient=(0, 0), transverse_error=0, offset=7):
        paths = []
        for name, axis, points in (('profile.xy', 'y', [(.5,.0025+i*.005) for i in range(20)]),
                                    ('pressure.xy', 'x', [(.1+i*.05,.05) for i in range(17)])):
            path = Path(root) / name
            lines = ['# '+axis+' p U_x U_y U_z']
            for x,y in points:
                v = analytic_velocity(spec, x, y)
                p = offset + gradient[0]*x + gradient[1]*y
                lines.append(' '.join(str(n) for n in (y if axis=='y' else x,p,v[0],v[1]+transverse_error,v[2])))
            path.write_text('\n'.join(lines)+'\n')
            paths.append(path)
        return paths

    def test_exact_two_mechanisms_and_constant_pressure_gauge(self):
        with tempfile.TemporaryDirectory() as root:
            for spec in DEVELOPMENT_TASKS:
                result = reference_measurements(*self.samples(root, spec), spec)
                self.assertLess(result['profile_error'], 1e-14)
                self.assertLess(result['pressure_gradient_error'], 1e-12)
                self.assertGreater(pressure_gradient_scale(spec), 0)

    def test_transverse_and_second_line_errors_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            for spec in DEVELOPMENT_TASKS:
                result = reference_measurements(*self.samples(root, spec, transverse_error=.002), spec)
                self.assertGreater(result['profile_error'], .02)
            profile, pressure = self.samples(root, DEVELOPMENT_TASKS[0])
            pressure.write_text(pressure.read_text().replace('0.01 0.0 0.0', '0.02 0.0 0.0'))
            self.assertGreater(reference_measurements(profile,pressure,DEVELOPMENT_TASKS[0])['profile_error'],.02)

    def test_both_pressure_directions_and_zero_reference_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            spec = DEVELOPMENT_TASKS[0]
            for gradient in ((.001,0),(0,.001)):
                result = reference_measurements(*self.samples(root,spec,gradient=gradient),spec)
                self.assertAlmostEqual(result['pressure_gradient_error'],.5,places=9)

    def test_bad_coordinates_nonfinite_or_missing_columns_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            spec = DEVELOPMENT_TASKS[1]
            for mode in ('coordinates','nan','missing_field'):
                profile,pressure = self.samples(root,spec)
                text = profile.read_text()
                if mode == 'coordinates':
                    text = text.replace('0.0025 ', '0.0035 ',1)
                elif mode == 'nan':
                    text = text.replace('7.0 ','nan ',1)
                else:
                    text = text.replace('U_y','absent_U_y')
                profile.write_text(text)
                with self.assertRaises(ValueError):
                    reference_measurements(profile,pressure,spec)

    def test_authored_boundaries_and_old_numerical_controls_preserved(self):
        template = ROOT / 'integration/foam_agent_v210/reference-corpus-v1/reference-case'
        with tempfile.TemporaryDirectory() as root:
            for spec in DEVELOPMENT_TASKS:
                case = create_reference(template,Path(root)/spec['id'],spec)
                for name in ('Allrun','system/fvSolution','system/fvSchemes','system/sample','constant/physicalProperties','constant/momentumTransport'):
                    self.assertEqual((case/name).read_bytes(),(template/name).read_bytes())
                self.assertEqual((case/'system/controlDict').read_text(),(template/'system/controlDict').read_text().replace('writeInterval 2000;','writeInterval 100;'))
                self.assertIn('north', (case/'0/U').read_text())
                self.assertIn('internalField uniform (0 0 0)', (case/'0/U').read_text())
            with self.assertRaises(ValueError):
                create_reference(template,Path(root)/'C0',DEVELOPMENT_TASKS[0])

    def test_task_declares_zero_gradient_metric_and_original_quality_thresholds(self):
        for spec in DEVELOPMENT_TASKS:
            declared = task(spec)
            criteria = {c['id']:c['target'] for c in declared['acceptance']}
            self.assertEqual(criteria['profile_error'],.02)
            self.assertEqual(criteria['pressure_gradient_error'],.03)
            self.assertEqual(criteria['local_continuity'],1e-7)
            self.assertFalse(declared['reference_contract']['zero_reference_division'])
            self.assertIn('steady_evidence',criteria)
            self.assertNotIn('steady_converged',criteria)
            self.assertIn('Render U as PNG',declared['objective'])

    def test_invalid_geometry_topology_and_nan_spec_rejected(self):
        for altered in (dict(DEVELOPMENT_TASKS[0],wall_speed=float('nan')),
                        dict(DEVELOPMENT_TASKS[0],ny=True),dict(DEVELOPMENT_TASKS[0],nx=0),
                        dict(DEVELOPMENT_TASKS[1],velocity=[.01,-.01,0]),
                        dict(DEVELOPMENT_TASKS[1],velocity=[.01,.01,.01]),
                        dict(DEVELOPMENT_TASKS[0],kind='unknown')):
            with self.assertRaises(ValueError):
                validate_spec(altered)

    def test_asymmetric_inlet_is_checked_against_actual_face_order(self):
        with tempfile.TemporaryDirectory() as root:
            mesh=Path(root)/'constant/polyMesh';mesh.mkdir(parents=True)
            spec=dict(DEVELOPMENT_TASKS[0],ny=4)
            points=[]
            for i in range(4):
                lo,hi=i*.025,(i+1)*.025
                points.extend([(0,lo,0),(0,lo,.01),(0,hi,.01),(0,hi,0)])
            (mesh/'points').write_text('16\n(\n'+'\n'.join('('+' '.join(map(str,p))+')' for p in points)+'\n)')
            faces=['4('+' '.join(str(j) for j in range(i*4,i*4+4))+')' for i in range(4)]
            (mesh/'faces').write_text('4\n(\n'+'\n'.join(faces)+'\n)')
            (mesh/'boundary').write_text('west { nFaces 4; startFace 0; }')
            self.assertTrue(couette_inlet_order(root,spec)['face_order_verified'])
            (mesh/'faces').write_text('4\n(\n'+'\n'.join(reversed(faces))+'\n)')
            with self.assertRaises(ValueError):
                couette_inlet_order(root,spec)

    def test_full_field_parser_handles_uniform_and_nonuniform_and_rejects_nan(self):
        self.assertEqual(field_values(b'internalField uniform (1 2 3);',2,3),[1,2,3,1,2,3])
        self.assertEqual(field_values(b'internalField nonuniform List<scalar> 2 (1 2);',2,1),[1,2])
        self.assertEqual(field_values(b'internalField nonuniform List<vector> 2 ((1 2 3) (4 5 6));',2,3),[1,2,3,4,5,6])
        for text,count,components in ((b'internalField uniform nan;',2,1),
                                      (b'internalField nonuniform List<scalar> 3 (1 2 3);',2,1),
                                      (b'internalField uniform (1 2);',2,3)):
            with self.assertRaises(ValueError):
                field_values(text,count,components)

    def test_stationarity_requires_two_positive_time_verified_full_fields(self):
        spec = DEVELOPMENT_TASKS[0]
        with patch('simclinic.domains.openfoam.reference_tasks.current_record',return_value=SimpleNamespace(data={'output_times':['100']})):
            self.assertIsNone(stationary_fields('synthetic',spec,{})['field_stationary'])
        with patch('simclinic.domains.openfoam.reference_tasks.current_record',return_value=SimpleNamespace(data={'output_times':['100','200']})), \
             patch('simclinic.domains.openfoam.reference_tasks.read_verified_result',side_effect=lambda record,name,*args: b'internalField uniform (0 0 0);' if name.endswith('/U') else b'internalField uniform 0;') as reads:
            result = stationary_fields('synthetic',spec,{'run_id':'synthetic-run','input_version':'synthetic-input'})
            self.assertTrue(result['field_stationary'])
            self.assertEqual(reads.call_count,4)
            self.assertEqual(result['compared_times'],['100','200'])
            self.assertTrue(all(call.args[-2:]==('synthetic-run','synthetic-input') for call in reads.call_args_list))

    def test_pressure_or_velocity_change_cannot_be_hidden_by_reference_accuracy(self):
        spec=DEVELOPMENT_TASKS[0]
        for changing in ('U','p'):
            def payload(record,name,*args):
                value = .0001 if name.startswith('200/') and name.endswith('/'+changing) else 0
                return ('internalField uniform ('+str(value)+' 0 0);' if name.endswith('/U') else 'internalField uniform '+str(value)+';').encode()
            with patch('simclinic.domains.openfoam.reference_tasks.current_record',return_value=SimpleNamespace(data={'output_times':['100','200']})), \
                 patch('simclinic.domains.openfoam.reference_tasks.read_verified_result',side_effect=payload):
                self.assertFalse(stationary_fields('synthetic',spec,{'run_id':'r','input_version':'v'})['field_stationary'])


if __name__ == '__main__':
    unittest.main()
