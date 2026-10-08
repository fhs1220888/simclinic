"""Shared nine-file v3 comparison on fresh C5/C6; never infer natural failure rates."""
from collections import Counter

DEVELOPMENT = [dict(id='C0-coarse',kind='couette',wall_speed=.02,nx=40,ny=20),
               dict(id='C0-fine',kind='couette',wall_speed=.02,nx=80,ny=40)]
HELDOUT = [dict(id='C5',kind='couette',wall_speed=.029,nx=54,ny=27),
           dict(id='C6',kind='couette',wall_speed=.037,nx=56,ny=28)]


def schedule():
    entries=[]
    for index,spec in enumerate(DEVELOPMENT):
        arms=('baseline','candidate') if index%2==0 else ('candidate','baseline')
        for arm in arms:
            entries.append(dict(id=spec['id']+'-'+arm,stage='development',spec=spec,
                                scenario='healthy',repeat=1,arm=arm))
    for index,spec in enumerate(HELDOUT):
        for repeat in range(1,4):
            for j,scenario in enumerate(('healthy','transient_missing')):
                arms=('baseline','candidate') if (index+repeat+j)%2==0 else ('candidate','baseline')
                for arm in arms:
                    entries.append(dict(id=f"{spec['id']}-r{repeat}-{scenario}-{arm}",stage='heldout',
                                        spec=spec,scenario=scenario,repeat=repeat,arm=arm))
    return entries


def budget_allows(known_tokens,development_tokens,stage,limits):
    reserve=limits['reserve_per_run_model_tokens']
    return (known_tokens+reserve<=limits['token_limit'] and
            (stage!='development' or development_tokens+reserve<=limits['development_token_limit']))


def outcomes(rows,complete=False):
    """All attempted held runs count; missing or failed quality is never a pass."""
    groups={}
    for row in rows:
        if row['stage']!='heldout':continue
        key=row['scenario']+'/'+row['arm']
        group=groups.setdefault(key,dict(attempted=0,quality_pass=0,false_completion=0,
                                       ineffective_retry=0,usage_unknown=0))
        group['attempted']+=1
        group['quality_pass']+=row.get('quality_met') is True
        group['false_completion']+=row.get('false_completion') is True
        group['ineffective_retry']+=row.get('retry_finding') is True
        group['usage_unknown']+=row.get('model_tokens') is None
    expected={scenario+'/'+arm:6 for scenario in ('healthy','transient_missing') for arm in ('baseline','candidate')}
    sufficient=complete and Counter(r['id'] for r in rows if r['stage']=='heldout')==Counter(e['id'] for e in schedule() if e['stage']=='heldout')
    sufficient=sufficient and all(groups.get(k,{}).get('attempted')==n and groups[k]['usage_unknown']==0 for k,n in expected.items())
    # A positive flag is restricted to the explicit missing-file challenge.
    conditional=sufficient and all(groups['healthy/'+arm]['quality_pass']==6 for arm in ('baseline','candidate'))
    conditional=conditional and all(g['false_completion']==0 for g in groups.values())
    conditional=conditional and groups['transient_missing/candidate']['quality_pass']>groups['transient_missing/baseline']['quality_pass']
    return dict(groups=groups,complete_predeclared_comparison=sufficient,
                conditional_recovery_benefit_observed=bool(conditional),
                natural_failure_rate_estimated=False,overall_optimization_proven=False,
                statistical_significance_claimed=False)
