"""Narrow numerical reproduction checks; software fixtures only, no CFD/model."""
import copy
import json
import math
import sys
import tempfile
import unittest

from scripts import review_recovery_observation as review
from simclinic.checks import behavior
from tests import test_recovery_observation_review as review_fixtures


def steps(value, count, direction=math.inf):
    for _ in range(count):
        value = math.nextafter(value, direction)
    return value


class DerivedFloatComparisonTests(unittest.TestCase):
    def compare(self, actual, archived, paths=review.MEASUREMENT_FLOAT_PATHS):
        differences = []
        review.require_derived_match(actual, archived, allowed_float_paths=paths,
                                     label='fixture', differences=differences)
        return differences

    def test_four_step_boundary_records_hex_and_preserves_archived_values(self):
        self.assertEqual(review.MAX_DERIVED_ULPS, 4)
        archived = dict(profile_error=0.01, pressure_gradient_error=0.02)
        before = copy.deepcopy(archived)
        actual = dict(archived, profile_error=steps(archived['profile_error'], 4))
        differences = self.compare(actual, archived)
        self.assertEqual(archived, before)
        self.assertEqual(len(differences), 1)
        entry = differences[0]
        self.assertEqual(entry['path'], 'fixture/profile_error')
        self.assertEqual(entry['ulp_distance'], 4)
        self.assertEqual(entry['allowed_ulps'], 4)
        self.assertTrue(entry['within_limit'])
        self.assertEqual(entry['archived_hex'], archived['profile_error'].hex())
        self.assertEqual(entry['recomputed_hex'], actual['profile_error'].hex())

    def test_fifth_step_is_rejected_and_difference_is_retained(self):
        differences = []
        with self.assertRaisesRegex(ValueError, 'exceeds ULP limit'):
            review.require_derived_match(dict(profile_error=steps(0.01, 5)),
                dict(profile_error=0.01), allowed_float_paths=review.MEASUREMENT_FLOAT_PATHS,
                label='fixture', differences=differences)
        self.assertEqual(differences[0]['ulp_distance'], 5)
        self.assertFalse(differences[0]['within_limit'])

    def test_ulp_distance_counts_binary_steps_across_binade(self):
        lower = math.nextafter(1.0, 0.0)
        upper = steps(lower, 5)
        self.assertEqual(review.ulp_distance(lower, upper), 5)
        with self.assertRaises(ValueError):
            self.compare(dict(profile_error=upper), dict(profile_error=lower))

    def test_negative_normal_derived_values_use_same_step_bound(self):
        value = -0.01
        self.assertEqual(review.ulp_distance(value, steps(value, 4, -math.inf)), 4)
        self.compare(dict(profile_error=steps(value, 4, -math.inf)), dict(profile_error=value))

    def test_type_shape_identity_hash_and_unlisted_float_are_exact(self):
        pairs = [
            (dict(profile_error=True), dict(profile_error=1.0)),
            (dict(profile_error=1), dict(profile_error=1.0)),
            (dict(profile_error=None), dict(profile_error=0.01)),
            (dict(profile_error=0.01, extra=1), dict(profile_error=0.01)),
            (dict(run_id='other'), dict(run_id='current')),
            (dict(sha256='b' * 64), dict(sha256='a' * 64)),
            (dict(attempt_count=True), dict(attempt_count=1)),
            (dict(elapsed_seconds=math.nextafter(1.0, math.inf)), dict(elapsed_seconds=1.0)),
            (dict(local_continuity=math.nextafter(1e-8, math.inf)), dict(local_continuity=1e-8)),
        ]
        for actual, archived in pairs:
            with self.subTest(actual=actual, archived=archived), self.assertRaises(ValueError):
                self.compare(actual, archived)

    def test_inlet_list_wildcards_only_cover_declared_arrays(self):
        archived = dict(west_face_y_centres=[0.01, 0.02],
                        expected_inlet_x_velocity=[0.001, 0.002], face_order_verified=True)
        actual = copy.deepcopy(archived)
        actual['west_face_y_centres'][1] = steps(0.02, 4)
        checked = self.compare(actual, archived, review.INLET_FLOAT_PATHS)
        self.assertEqual(checked[0]['path'], 'fixture/west_face_y_centres/1')
        for change in ('length', 'bool', 'array_type'):
            bad = copy.deepcopy(archived)
            if change == 'length':
                bad['west_face_y_centres'].append(0.03)
            elif change == 'bool':
                bad['face_order_verified'] = 1
            else:
                bad['expected_inlet_x_velocity'] = {'0': 0.001, '1': 0.002}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.compare(bad, archived, review.INLET_FLOAT_PATHS)

    def test_wildcard_cannot_match_a_dictionary_asterisk_key(self):
        with self.assertRaises(ValueError):
            self.compare({'values': {'*': math.nextafter(1.0, math.inf)}},
                         {'values': {'*': 1.0}}, (('values', '*'),))

    def test_white_list_is_not_recursive_or_a_field_name_search(self):
        with self.assertRaises(ValueError):
            self.compare({'nested': {'profile_error': math.nextafter(0.01, math.inf)}},
                         {'nested': {'profile_error': 0.01}})

    def test_zero_subnormal_sign_changes_and_nonfinite_differences_are_rejected(self):
        pairs = [(0.0, -0.0), (0.0, math.nextafter(0.0, 1.0)),
                 (sys.float_info.min, math.nextafter(sys.float_info.min, 0.0)),
                 (-0.01, 0.01), (math.inf, math.inf), (-math.inf, -math.inf),
                 (math.nan, math.nan)]
        for actual, archived in pairs:
            with self.subTest(actual=actual, archived=archived), self.assertRaises(ValueError):
                self.compare(dict(profile_error=actual), dict(profile_error=archived))
        # Identical finite values are exact comparison, not a relaxed ULP rule.
        for value in (0.0, -0.0, math.nextafter(0.0, 1.0)):
            self.assertEqual(self.compare(dict(profile_error=value), dict(profile_error=value)), [])

    def test_stationarity_scales_thresholds_and_scoring_remain_exact(self):
        for field, value in [('velocity_change_scaled', 1e-8), ('pressure_change_scaled', 1e-8),
                             ('velocity_scale', 0.024), ('field_change_tolerance', 1e-7),
                             ('target', 0.02), ('success_fraction', 0.5)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.require_exact({field: math.nextafter(value, math.inf)}, {field: value}, field)
        for a, b in [(True, 1), (0, 0.0), (False, None)]:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                review.require_exact({'value': a}, {'value': b}, 'strict')

    def test_each_threshold_decision_is_exact_even_when_both_tasks_fail(self):
        criteria = [dict(id='profile_error', operator='le', target=0.02),
                    dict(id='pressure_gradient_error', operator='le', target=0.03)]
        archived = dict(profile_error=0.02, pressure_gradient_error=0.5)
        actual = dict(archived, profile_error=math.nextafter(0.02, math.inf))
        self.compare(actual, archived)  # One step is numerically admissible.
        self.assertFalse(all(behavior.matches(actual[c['id']], c) for c in criteria))
        self.assertFalse(all(behavior.matches(archived[c['id']], c) for c in criteria))
        with self.assertRaisesRegex(ValueError, 'profile_error'):
            review.require_same_decisions(actual, archived, criteria, behavior)

    def test_same_failed_or_unknown_decisions_are_supported_without_success(self):
        criteria = [dict(id='profile_error', operator='le', target=0.02)]
        decisions = review.require_same_decisions(dict(profile_error=0.1),
            dict(profile_error=math.nextafter(0.1, math.inf)), criteria, behavior)
        self.assertIs(decisions['profile_error'], False)
        decisions = review.require_same_decisions(dict(profile_error=None),
            dict(profile_error=None), criteria, behavior)
        self.assertIsNone(decisions['profile_error'])
        with self.assertRaises(ValueError):
            review.require_same_decisions(dict(profile_error=0.01), dict(profile_error=None), criteria, behavior)

    def test_unknown_boolean_decision_cannot_be_replaced_by_false(self):
        criterion = [dict(id='rendered_velocity', operator='eq', target=True)]
        with self.assertRaises(ValueError):
            review.require_same_decisions(dict(rendered_velocity=False),
                                          dict(rendered_velocity=None), criterion, behavior)


class EarlyAcceptanceFailureTests(unittest.TestCase):
    def refresh_registered_file(self, item, record):
        item['sha256'] = review.sha(item['file'])
        for registered in record['artifacts']:
            if registered['id'] == item['id']:
                registered['sha256'] = item['sha256']

    def replace_registered_json(self, item, value, record):
        item['file'].write_text(json.dumps(value))
        self.refresh_registered_file(item, record)

    def test_sampling_error_preserves_none_and_honest_failed_quality(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = review_fixtures.RecoveryObservationReviewTests()
            directory, row, record, items, readers, definition, _ = fixture.acceptance_fixture(root)
            assessment = next(a for a in items.values() if a['path'] == 'observations/task-assessment.json')
            data = json.loads(assessment['file'].read_text())
            data['values'].update(profile_error=None, pressure_gradient_error=None)
            data['error'] = dict(type='SamplingError', code='sampling_missing_output', message='Software fixture')
            self.replace_registered_json(assessment, data, record)
            record['events'] = []
            record['collection_status'] = 'failed'
            row.update(quality_met=False, acceptance_verified=False)
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertIsNone(checked['values']['profile_error'])
            self.assertIsNone(checked['values']['pressure_gradient_error'])
            self.assertIsNone(checked['criterion_decisions']['profile_error'])
            self.assertFalse(checked['accepted'])
            self.assertFalse(checked['full_quality'])

    def test_final_measurement_small_float_difference_retains_same_quality(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = review_fixtures.RecoveryObservationReviewTests()
            directory, row, record, items, readers, definition, paths = fixture.acceptance_fixture(root)
            lines = paths[0].read_text().splitlines()
            first = lines[1].split()
            first[3] = '0.000001'  # Small all-component deviation, still accepted.
            lines[1] = ' '.join(first)
            paths[0].write_text('\n'.join(lines) + '\n')
            for item in items.values():
                if item['file'] == paths[0]:
                    self.refresh_registered_file(item, record)
            original = readers['reference'].reference_measurements(*paths, definition['spec'])
            assessment = next(a for a in items.values() if a['path'] == 'observations/task-assessment.json')
            assessed = json.loads(assessment['file'].read_text())
            assessed['values'].update(original)
            self.replace_registered_json(assessment, assessed, record)
            gate = next(a for a in items.values() if a['path'] == 'observations/sampling/gate-assessment.json')
            data = json.loads(gate['file'].read_text())
            data['measurements'] = original
            for output in data['outputs']:
                output['sha256'] = items[output['artifact_id']]['sha256']
            value = data['measurements']['profile_error']
            self.assertGreater(value, sys.float_info.min)
            data['measurements']['profile_error'] = steps(value, 4)
            self.replace_registered_json(gate, data, record)
            self.assertFalse(review.artifacts(directory, record)[2])
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'pass', checked)
            self.assertTrue(checked['full_quality'])
            self.assertTrue(any(d['path'] == 'final_sample_measurements/profile_error'
                                and d['ulp_distance'] == 4 for d in checked['numeric_differences']))

    def test_changed_original_acceptance_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = review_fixtures.RecoveryObservationReviewTests()
            directory, row, record, items, readers, definition, _ = fixture.acceptance_fixture(root)
            assessment = next(a for a in items.values() if a['path'] == 'observations/task-assessment.json')
            data = json.loads(assessment['file'].read_text())
            criterion = next(c for c in data['criteria'] if c['id'] == 'profile_error')
            criterion['target'] = math.nextafter(criterion['target'], math.inf)
            self.replace_registered_json(assessment, data, record)
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'fail', checked)
            self.assertIn('acceptance_criteria', checked['reason'])

    def test_stationarity_single_step_difference_is_rejected_in_full_review(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = review_fixtures.RecoveryObservationReviewTests()
            directory, row, record, items, readers, definition, _ = fixture.acceptance_fixture(root)
            context = {k: row[k] for k in ('run_id', 'input_version')}
            def save(relative, text):
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                identifier = 'stationarity-' + str(len(items))
                item = dict(id=identifier, path=relative, file=path, sha256=review.sha(path), **context)
                items[identifier] = item
                record['artifacts'].append({k: v for k, v in item.items() if k != 'file'})
            native = directory / 'attempts/native-run/native/record.json'
            data = json.loads(native.read_text())
            data['output_times'] = ['100', '200']
            native.write_text(json.dumps(data))
            delta = 1e-10
            for time, velocity in [('100', 0.0), ('200', delta)]:
                save('attempts/native-run/outputs/' + time + '/U',
                     'internalField uniform (' + format(velocity, '.17g') + ' 0 0);\n')
                save('attempts/native-run/outputs/' + time + '/p', 'internalField uniform 0;\n')
            ratio = delta / definition['task']['reference_contract']['velocity_scale']
            save('observations/field-stationarity.json', json.dumps(dict(field_stationary=True,
                 velocity_change_scaled=math.nextafter(ratio, math.inf), pressure_change_scaled=0.0,
                 compared_times=['100', '200'])))
            checked = review.acceptance_review(directory, row, record, items, readers, definition)
            self.assertEqual(checked['status'], 'fail', checked)
            self.assertIn('stationarity/velocity_change_scaled', checked['reason'])


if __name__ == '__main__':
    unittest.main()
