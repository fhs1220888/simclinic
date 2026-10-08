from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from simclinic.domains.openfoam.input_contract import (
    FILES, POLICY, ContractReferenceCompletion, compare_case, compare_input,
    contract_task, literal_dictionary, precision_bound)
from simclinic.domains.openfoam.quantity_inputs import compare_case as old_compare
from simclinic.domains.openfoam.reference_tasks import create_reference, task
from simclinic.evaluation.input_contract_validation import (
    DEVELOPMENT_SPECS, NEGATIVES, apply_positive, apply_negative, high_precision_inlet, positive_text, schedule)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT/'integration/foam_agent_v210/reference-corpus-v1/reference-case'


class InputContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = DEVELOPMENT_SPECS[0]
        self.ref = create_reference(TEMPLATE, Path(self.tmp.name)/'reference', self.spec)
        self.case = Path(self.tmp.name)/'case'; shutil.copytree(self.ref, self.case)

    def compare(self, name):
        return compare_input(self.case/name, self.ref/name, name, self.spec)

    def edit(self, name, old, new):
        p = self.case/name; p.write_text(p.read_text().replace(old, new, 1))

    def test_each_of_nine_files_accepts_equivalent_literal_and_metadata(self):
        for name in FILES:
            with self.subTest(file=name):
                p = self.case/name; p.write_text(positive_text(p.read_text(), name))
                result = self.compare(name)
                self.assertEqual(result['status'], 'pass')
                self.assertTrue(result['matched'])
                self.assertNotEqual(result['actual_sha256'], result['expected_sha256'])

    def test_each_of_nine_files_rejects_declared_negative(self):
        for name in FILES:
            with self.subTest(file=name):
                apply_negative(self.case, name)
                result = self.compare(name)
                self.assertEqual(result['status'], 'finding')
                self.assertIs(result['matched'], False)

    def test_both_precision_boundaries_accept_template_and_exact_formula(self):
        for spec in DEVELOPMENT_SPECS:
            with self.subTest(spec=spec):
                ref = create_reference(TEMPLATE, Path(self.tmp.name)/spec['id'], spec)
                checks, _ = compare_case(ref, ref, spec)
                self.assertTrue(all(checks.values()))
                high_precision_inlet(ref/'0/U', spec)
                checked = compare_input(ref/'0/U', self.ref/'0/U' if spec==self.spec else create_reference(TEMPLATE, Path(self.tmp.name)/'C4-template', spec)/'0/U', '0/U', spec)
                self.assertTrue(checked['matched'])
                self.assertTrue(all(r['matched'] for r in checked['inlet']['faces']))

    def test_half_ulp_is_exact_inclusive_and_rejects_just_outside(self):
        exact = Fraction('0.022')/46
        bound = precision_bound(exact)
        self.assertEqual(bound, Fraction(1, 2*10**15))
        # Use a terminating face value to express both boundary points exactly.
        spec = dict(self.spec, wall_speed=0.023)
        ref = create_reference(TEMPLATE, Path(self.tmp.name)/'terminating', spec)
        original = (ref/'0/U').read_text()
        for value, outcome in [('0.0005000000000005', True), ('0.0005000000000005001', False),
                               ('0.0004999999999995', True), ('0.0004999999999994999', False)]:
            # Both sides use the order of the exact .0005, not candidate magnitude.
            p = self.case/'0/U'; p.write_text(original.replace('(0.0005 0 0)', '('+value+' 0 0)', 1))
            self.assertIs(compare_input(p, ref/'0/U', '0/U', spec)['matched'], outcome)

    def test_no_tolerance_for_other_boundary_component_dimension_or_count(self):
        original = (self.case/'0/U').read_text()
        changes = [('0.022 0 0', '0.02200000000001 0 0'),
                   ('0.000478260869565 0 0', '0.000478260869565 1e-20 0'),
                   ('dimensions [0 1 -1', 'dimensions [0 2 -1'),
                   ('List<vector> 23', 'List<vector> 22'),
                   ('type zeroGradient;', 'type fixedValue;')]
        for old, new in changes:
            with self.subTest(change=new):
                (self.case/'0/U').write_text(original.replace(old, new, 1))
                self.assertIs(self.compare('0/U')['matched'], False)

    def test_scalar_controls_no_old_rounding_mask_even_tiny_changes(self):
        for name, old, new in [('system/sample', '0.0025', '0.00250000000001'),
                               ('system/fvSolution', 'p 1e-7;', 'p 1.00000001e-7;'),
                               ('system/blockMeshDict', '0.1', '0.10000000000001')]:
            with self.subTest(file=name):
                self.edit(name, old, new); self.assertIs(self.compare(name)['matched'], False)

    def test_missing_symlink_duplicate_directive_nonfinite_and_expressions_unknown(self):
        original = (self.case/'0/p').read_text()
        for change in ('#include "override"', 'internalField uniform 0;', '/* unterminated'):
            (self.case/'0/p').write_text(original+'\n'+change)
            self.assertIsNone(self.compare('0/p')['matched'])
        for value in ('$x', '#calc "0"', 'nan', '1e999', '"0"', '(0 + 0)', '+0.00'):
            (self.case/'0/p').write_text(original.replace('internalField uniform 0;', 'internalField uniform '+value+';'))
            self.assertIsNone(self.compare('0/p')['matched'])
        p = self.case/'0/p'; p.unlink()
        self.assertIsNone(self.compare('0/p')['matched'])
        p.symlink_to(self.ref/'0/p'); self.assertIsNone(self.compare('0/p')['matched'])

    def test_duplicate_nested_and_named_entries_are_unknown(self):
        self.edit('0/U', 'east {type zeroGradient;}', 'east {type zeroGradient; type zeroGradient;}')
        self.assertIsNone(self.compare('0/U')['matched'])
        self.edit('system/sample', 'pressure {', 'profile {')
        self.assertIsNone(self.compare('system/sample')['matched'])

    def test_header_identity_and_quoted_comment_text_stay_checked(self):
        self.edit('0/p', 'class volScalarField;', 'class volVectorField;')
        self.assertIs(self.compare('0/p')['matched'], False)
        self.edit('system/sample', '"libsampling.so"', '"libsampling.so//not-a-comment"')
        self.assertIs(self.compare('system/sample')['matched'], False)

    def test_integer_reader_labels_cannot_be_scientific_or_decimal(self):
        for name, old, new in [('system/sample', 'nPoints 20;', 'nPoints 2e1;'),
                               ('system/blockMeshDict', '(46 23 1)', '(46.0 23 1)'),
                               ('0/U', 'List<vector> 23', 'List<vector> 23.0')]:
            self.edit(name, old, new); self.assertIsNone(self.compare(name)['matched'])

    def test_optional_nu_dimensions_exact_and_wrong_dimensions_rejected(self):
        self.edit('constant/physicalProperties', 'nu 0.001;', 'nu [0 2 -1 0 0 0 0] 1e-3;')
        self.assertTrue(self.compare('constant/physicalProperties')['matched'])
        self.edit('constant/physicalProperties', '[0 2 -1', '[0 2 -2')
        self.assertFalse(self.compare('constant/physicalProperties')['matched'])

    def test_explicit_policy_required_thresholds_legacy_and_raw_identity_preserved(self):
        apply_positive(self.case, self.spec, True)
        self.assertFalse(old_compare(self.case, self.ref)[0]['0/U'])
        recorder = SimpleNamespace(data={'task': contract_task(self.spec)})
        policy = ContractReferenceCompletion(recorder, self.ref, 'fixed', self.spec)
        self.assertTrue(all(policy.assess_inputs(self.case)[0].values()))
        self.assertEqual(task(self.spec)['acceptance'], contract_task(self.spec)['acceptance'])
        recorder.data['task'] = task(self.spec)
        with self.assertRaises(ValueError): policy.assess_inputs(self.case)
        recorder.data['task'] = contract_task(self.spec)
        recorder.data['task']['reference_contract']['inlet_significant_digits'] = 11
        with self.assertRaises(ValueError): policy.assess_inputs(self.case)

    def test_known_24_archives_are_development_only_no_historical_regrade(self):
        batch = ROOT/'real-runs/full-model-quantity-recovery-study-20261006'
        if not batch.exists(): self.skipTest('Optional archived development data')
        original = hashlib.sha256((batch/'results.json').read_bytes()).hexdigest()
        count = 0
        for entry in json.loads((batch/'freeze.json').read_text())['entries']:
            if entry['id'].startswith('C0-'): continue
            out = batch/'runs'/entry['id']; record = json.loads((out/'record.json').read_text())
            checks, evidence = compare_case(out/record['attempts'][-1]['input_snapshot'], batch/'definitions'/entry['id']/'reference', entry['spec'])
            self.assertTrue(all(checks.values()))
            self.assertEqual(len(evidence['files']), 9)
            self.assertFalse(json.loads((out/'summary.json').read_text())['quality_met'])
            count += 1
        self.assertEqual(count, 24)
        self.assertEqual(hashlib.sha256((batch/'results.json').read_bytes()).hexdigest(), original)

    def test_real_schedule_is_predeclared_four_positive_ten_negative_zero_model(self):
        entries = schedule()
        self.assertEqual(len(entries), 14)
        self.assertEqual(sum(e['expected_quality'] for e in entries), 4)
        self.assertEqual({e['changed_file'] for e in entries if e['changed_file']}, set(FILES))
        self.assertEqual(len(set(e['id'] for e in entries)), 14)

    def test_undeclared_input_file_and_malformed_nu_are_unknown(self):
        (self.case/'system/override').write_text('nu 0.001;')
        checks, evidence = compare_case(self.case, self.ref, self.spec)
        self.assertIsNone(checks['system/override'])
        self.assertEqual(evidence['files']['system/override']['reason'], 'undeclared_input_file')
        self.edit('constant/physicalProperties', 'nu 0.001;', 'nu [0 2 -1] 0.001;')
        self.assertIsNone(self.compare('constant/physicalProperties')['matched'])


if __name__ == '__main__': unittest.main()
