import unittest
from simclinic.evaluation.contract_recovery_study import schedule, outcomes, budget_allows
from simclinic.evaluation.recovery_study import schedule as first_schedule
from simclinic.evaluation.quantity_recovery_study import schedule as second_schedule
from simclinic.domains.openfoam.input_contract import contract_task, POLICY
from simclinic.domains.openfoam.reference_tasks import task


class ContractRecoveryStudyTests(unittest.TestCase):
    def test_fresh_parameters_balanced_pairs_same_explicit_acceptance(self):
        rows=schedule()
        self.assertEqual(len(rows),28)
        self.assertEqual(len({e['id'] for e in rows}),28)
        self.assertEqual(rows[:4],second_schedule()[:4])
        known={(e['spec']['wall_speed'],e['spec']['nx'],e['spec']['ny']) for e in first_schedule()+second_schedule()}
        self.assertTrue(all((e['spec']['wall_speed'],e['spec']['nx'],e['spec']['ny']) not in known for e in rows[4:]))
        self.assertEqual({e['spec']['id'] for e in rows[4:]},{'C5','C6'})
        for row in rows:
            self.assertEqual(task(row['spec'])['acceptance'],contract_task(row['spec'])['acceptance'])
            self.assertEqual(contract_task(row['spec'])['reference_contract']['input_matching_policy'],POLICY)
        for i in range(4,28,2):
            a,b=rows[i:i+2]
            self.assertEqual(a['spec'],b['spec'])
            self.assertEqual(a['scenario'],b['scenario'])
            self.assertEqual(a['repeat'],b['repeat'])
            self.assertEqual({a['arm'],b['arm']},{'baseline','candidate'})

    def test_quality_and_complete_denominator_precede_conditional_benefit(self):
        rows=[dict(**e,quality_met=e['scenario']=='healthy' or e['arm']=='candidate',false_completion=False,retry_finding=False,model_tokens=1) for e in schedule()]
        self.assertTrue(outcomes(rows,True)['conditional_recovery_benefit_observed'])
        self.assertFalse(outcomes(rows,False)['conditional_recovery_benefit_observed'])
        self.assertFalse(outcomes(rows[:-1],True)['complete_predeclared_comparison'])
        self.assertFalse(outcomes(rows+[rows[-1]],True)['complete_predeclared_comparison'])
        old=[dict(**e,quality_met=True,model_tokens=1) for e in second_schedule()]
        self.assertFalse(outcomes(old,True)['complete_predeclared_comparison'])
        rows[4]['quality_met']=False
        self.assertFalse(outcomes(rows,True)['conditional_recovery_benefit_observed'])
        self.assertFalse(outcomes(rows,True)['overall_optimization_proven'])

    def test_unknown_usage_and_false_completion_prevent_benefit(self):
        rows=[dict(**e,quality_met=True,false_completion=False,retry_finding=False,model_tokens=1) for e in schedule()]
        rows[4]['model_tokens']=None
        self.assertFalse(outcomes(rows,True)['complete_predeclared_comparison'])
        rows[4]['model_tokens']=1;rows[4]['false_completion']=True
        self.assertFalse(outcomes(rows,True)['conditional_recovery_benefit_observed'])

    def test_budget_reserve_is_enforced_before_next_call(self):
        limits=dict(token_limit=7_500_000,development_token_limit=1_500_000,reserve_per_run_model_tokens=450_000)
        self.assertTrue(budget_allows(7_050_000,900_000,'heldout',limits))
        self.assertFalse(budget_allows(7_050_001,900_000,'heldout',limits))
        self.assertFalse(budget_allows(1_050_001,1_050_001,'development',limits))


if __name__=='__main__':unittest.main()
