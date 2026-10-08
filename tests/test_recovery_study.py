import tempfile
from pathlib import Path
import unittest

from simclinic.evaluation.recovery_study import schedule,outcomes,budget_allows
from scripts.run_recovery_study import build_corpus,verify_freeze


def results():
    return [dict(**e,quality_met=e['scenario']=='healthy' or e['arm']=='candidate',
                 false_completion=False,retry_finding=False,model_tokens=10) for e in schedule()]


class RecoveryStudyTests(unittest.TestCase):
    def test_healthy_development_before_held_and_no_old_reuse(self):
        entries=schedule()
        self.assertEqual(len(entries),28)
        self.assertEqual(len({e['id'] for e in entries}),28)
        self.assertTrue(all(e['stage']=='development' and e['scenario']=='healthy' for e in entries[:4]))
        held=entries[4:]
        self.assertEqual({e['spec']['id'] for e in held},{'C1','C2'})
        self.assertEqual({e['repeat'] for e in held},{1,2,3})
        self.assertEqual(outcomes(results(),True)['groups']['healthy/candidate']['attempted'],6)

    def test_conditional_benefit_is_never_overall_or_natural(self):
        scored=outcomes(results(),True)
        self.assertTrue(scored['conditional_recovery_benefit_observed'])
        for key in ('overall_optimization_proven','natural_failure_rate_estimated','statistical_significance_claimed'):
            self.assertFalse(scored[key])
        self.assertFalse(outcomes(results(),False)['conditional_recovery_benefit_observed'])

    def test_missing_failed_and_unknown_runs_cannot_be_dropped(self):
        rows=results();rows[-1]['quality_met']=None;rows[-1]['model_tokens']=None
        scored=outcomes(rows,True)
        self.assertEqual(sum(g['attempted'] for g in scored['groups'].values()),24)
        self.assertFalse(scored['complete_predeclared_comparison'])
        self.assertFalse(outcomes(rows[:-1],True)['conditional_recovery_benefit_observed'])

    def test_duplicate_ids_do_not_masquerade_as_complete(self):
        rows=results();rows[-1]=dict(rows[-3])
        self.assertFalse(outcomes(rows,True)['complete_predeclared_comparison'])

    def test_quality_regression_and_false_completion_prevent_positive_flag(self):
        rows=results();healthy=next(r for r in rows if r['stage']=='heldout' and r['scenario']=='healthy' and r['arm']=='candidate')
        healthy['quality_met']=False
        self.assertFalse(outcomes(rows,True)['conditional_recovery_benefit_observed'])
        rows=results();rows[-1]['false_completion']=True
        self.assertFalse(outcomes(rows,True)['conditional_recovery_benefit_observed'])

    def test_failed_usage_and_development_reserve_count_toward_limits(self):
        limits=dict(token_limit=100,development_token_limit=40,reserve_per_run_model_tokens=20)
        self.assertTrue(budget_allows(80,20,'heldout',limits))
        self.assertFalse(budget_allows(81,20,'heldout',limits))
        self.assertFalse(budget_allows(30,21,'development',limits))
        self.assertTrue(budget_allows(30,21,'heldout',limits))

    def test_shared_retrieval_contains_only_development_task(self):
        import json
        from scripts.run_full_agent import CORPUS
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'corpus'
            build_corpus(destination,Path(CORPUS),Path('development-evidence'))
            corpus=json.loads((destination/'corpus.json').read_text())
            self.assertFalse(corpus['heldout_reference_or_outputs_in_corpus'])
            docs=corpus['collections']['openfoam_tutorials_details']
            self.assertEqual(len(docs),1)
            self.assertIn('validated-couette-C0',docs[0]['id'])
            self.assertIn('wall',docs[0]['full_content'])
            self.assertNotIn('.026',docs[0]['full_content'])
            self.assertNotIn('.031',docs[0]['full_content'])

    def test_challenge_call_contract_accepts_actual_recorder_first(self):
        # Exercise the actual context manager API, including its fault path.
        from simclinic.evaluation.sampling_challenge import sampling_challenge
        with sampling_challenge(object(),'healthy'):pass
        with sampling_challenge(object(),'transient_missing'):pass


if __name__=='__main__':unittest.main()
