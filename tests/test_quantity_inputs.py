from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.quantity_inputs import (
    POLICY, compare_input, compare_case, quantity_task, QuantityReferenceCompletion,
    RecoveringQuantityReferenceCompletion)
from simclinic.domains.openfoam.reference_tasks import ReferenceTaskCompletion, create_reference, DEVELOPMENT_TASKS, task
from simclinic.domains.openfoam.sampling_recovery import BoundedSamplingRecovery

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT/'integration/foam_agent_v210/reference-corpus-v1/reference-case'


class QuantityInputTests(unittest.TestCase):
    def compare(self, nu, extra=''):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'physicalProperties'
            p.write_text('viscosityModel constant; nu '+nu+';'+extra)
            return compare_input(p, TEMPLATE/'constant/physicalProperties', 'constant/physicalProperties')

    def test_equivalent_decimal_literals_and_optional_dimensions(self):
        for value in ('0.001', '1e-3', '[0 2 -1 0 0 0 0] 0.001', '[0.0 2 -1 0 0 0 0] 1e-3'):
            result = self.compare(value)
            self.assertEqual(result['status'], 'pass')
            self.assertTrue(result['matched'])
            self.assertEqual(result['reason'], 'same_quantity')
            self.assertTrue(result['actual_sha256'])

    def test_wrong_dimensions_and_value_even_below_old_rounding_are_findings(self):
        for value, reason in (('[0 2 -2 0 0 0 0] .001', 'wrong_dimensions'),
                              ('.002', 'nu_value_changed'), ('0', 'nu_value_changed'),
                              ('0.00100000000001', 'nu_value_changed')):
            result = self.compare(value)
            self.assertEqual(result['reason'], reason)
            self.assertIs(result['matched'], False)
            self.assertEqual(result['status'], 'finding')

    def test_complex_malformed_duplicate_and_nonfinite_are_unknown_never_pass(self):
        for value, extra in (('$nu', ''), ('#calc "0.001"', ''), ('nan', ''),
                             ('[0 2 -1] .001', ''), ('.001', 'nu .002;'),
                             ('.001', '#include "override"'), ('.001', 'unknown 1;'),
                             ('.001', '/* unterminated'), ('"/*" .001', '')):
            result = self.compare(value, extra)
            self.assertEqual(result['status'], 'insufficient_evidence')
            self.assertIsNone(result['matched'])

    def test_comments_allowed_but_model_change_rejected(self):
        self.assertTrue(self.compare('/* dimension omitted */ 1e-3', '// comment')['matched'])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'physicalProperties'
            p.write_text('viscosityModel Newtonian; nu .001;')
            self.assertEqual(compare_input(p, TEMPLATE/'constant/physicalProperties', 'constant/physicalProperties')['reason'], 'viscosity_model_changed')

    def test_missing_and_symlink_have_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'missing'
            self.assertIsNone(compare_input(p, TEMPLATE/'constant/physicalProperties', 'constant/physicalProperties')['matched'])
            p.symlink_to(TEMPLATE/'constant/physicalProperties')
            self.assertEqual(compare_input(p, TEMPLATE/'constant/physicalProperties', 'constant/physicalProperties')['status'], 'insufficient_evidence')

    def test_policy_is_opt_in_other_inputs_and_legacy_rules_preserved(self):
        spec = DEVELOPMENT_TASKS[0]
        self.assertEqual(task(spec)['acceptance'], quantity_task(spec)['acceptance'])
        with tempfile.TemporaryDirectory() as tmp:
            ref = create_reference(TEMPLATE, Path(tmp)/'reference', spec)
            copy = Path(tmp)/'copy'; shutil.copytree(ref, copy)
            p = copy/'constant/physicalProperties'
            p.write_text(p.read_text().replace('nu 0.001;', 'nu [0 2 -1 0 0 0 0] 0.001;'))
            self.assertNotEqual(canonical_dictionary(p), canonical_dictionary(ref/'constant/physicalProperties'))
            legacy = ReferenceTaskCompletion(SimpleNamespace(), ref, 'fixed', spec)
            self.assertFalse(legacy.assess_inputs(copy)[0]['constant/physicalProperties'])
            recorder = SimpleNamespace(data={'task': quantity_task(spec)})
            policy = QuantityReferenceCompletion(recorder, ref, 'fixed', spec)
            checks, evidence = policy.assess_inputs(copy)
            self.assertTrue(all(checks.values()))
            self.assertEqual(evidence['policy'], POLICY)
            control = copy/'system/fvSolution'
            control.write_text(control.read_text().replace('1e-7', '1e-6'))
            self.assertFalse(policy.assess_inputs(copy)[0]['system/fvSolution'])
            recorder.data['task'] = task(spec)
            with self.assertRaises(ValueError): policy.assess_inputs(copy)
        self.assertTrue(issubclass(RecoveringQuantityReferenceCompletion, BoundedSamplingRecovery))

    def test_three_observed_old_mismatches_are_development_regressions_only(self):
        batch = ROOT/'real-runs/full-model-recovery-study-20261005'
        if not batch.exists(): self.skipTest('Optional archived development data')
        import json
        original = (batch/'results.json').read_bytes()
        for rid in ('C1-r2-transient_missing-baseline', 'C1-r3-healthy-candidate', 'C1-r3-transient_missing-candidate'):
            out = batch/'runs'/rid
            record = json.loads((out/'record.json').read_text())
            checks, evidence = compare_case(out/record['attempts'][-1]['input_snapshot'], batch/'definitions'/rid/'reference')
            self.assertTrue(all(checks.values()))
            self.assertTrue(evidence['files']['constant/physicalProperties']['explicit_dimensions'])
            self.assertFalse(json.loads((out/'summary.json').read_text())['quality_met'])
        self.assertEqual((batch/'results.json').read_bytes(), original)


if __name__ == '__main__': unittest.main()
