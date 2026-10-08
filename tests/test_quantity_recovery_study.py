import unittest
from simclinic.evaluation.quantity_recovery_study import schedule, outcomes
from simclinic.evaluation.recovery_study import schedule as old_schedule
from simclinic.domains.openfoam.quantity_inputs import quantity_task, POLICY
from simclinic.domains.openfoam.reference_tasks import task


class QuantityRecoveryStudyTests(unittest.TestCase):
    def test_new_parameter_tasks_and_same_contract_both_arms(self):
        new = schedule(); old = old_schedule()
        self.assertEqual(len(new),28)
        self.assertEqual(len({e['id'] for e in new}),28)
        self.assertEqual(new[:4],old[:4])
        self.assertEqual({e['spec']['id'] for e in new[4:]},{'C3','C4'})
        old_parameters={(e['spec']['wall_speed'],e['spec']['nx'],e['spec']['ny']) for e in old}
        self.assertTrue(all((e['spec']['wall_speed'],e['spec']['nx'],e['spec']['ny']) not in old_parameters for e in new[4:]))
        for e in new:
            self.assertEqual(task(e['spec'])['acceptance'],quantity_task(e['spec'])['acceptance'])
            self.assertEqual(quantity_task(e['spec'])['reference_contract']['input_matching_policy'],POLICY)

    def test_old_or_missing_results_cannot_satisfy_new_denominator(self):
        rows=[dict(**e,quality_met=e['scenario']=='healthy' or e['arm']=='candidate',false_completion=False,retry_finding=False,model_tokens=1) for e in schedule()]
        result=outcomes(rows,True)
        self.assertTrue(result['conditional_recovery_benefit_observed'])
        self.assertFalse(result['overall_optimization_proven'])
        self.assertFalse(outcomes(rows[:-1],True)['complete_predeclared_comparison'])
        old=[dict(**e,quality_met=True,model_tokens=1) for e in old_schedule()]
        self.assertFalse(outcomes(old,True)['complete_predeclared_comparison'])
        rows[4]['quality_met']=False
        self.assertFalse(outcomes(rows,True)['conditional_recovery_benefit_observed'])


if __name__=='__main__': unittest.main()
