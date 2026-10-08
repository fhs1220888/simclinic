"""Offline review including stopped reference batches and expected failures."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import hashes,verify_hashes
from simclinic.domains.openfoam.reference_tasks import reference_measurements,stationary_fields,couette_inlet_order
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.checks.behavior import matches,retries
from simclinic.result_guard import read_verified_result


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(batch):
    f,p=read(batch/'freeze.json'),read(batch/'progress.json')
    digest=sha(batch/'freeze.json')
    if digest!=(batch/'freeze.sha256').read_text().strip() or p['freeze_sha256']!=digest or p['status'] not in ('complete','reference_contract_failed'):
        raise ValueError('Closed intact development batch required')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):
        verify_hashes(batch/folder,f[key])
    if [r['id'] for r in p['runs']]!=f['schedule'][:len(p['runs'])]:raise ValueError('Wrong actual schedule')
    totals=dict(artifacts=0,input_snapshots=0,solver_runs=0,model_calls=0,sampling_calls=0)
    rows,identities=[],set()
    for row in p['runs']:
        out=batch/'runs'/row['id'];s=read(out/'summary.json');record=load_record(out/'record.json',out)
        if s['new_model_calls']!=0 or record.data['model_calls'] or record.data['metrics']['model_tokens']!=0:
            raise ValueError('Unexpected model activity')
        if record.data['heldout'] is not False or s['attempts']!=1:raise ValueError('Wrong development/solve label')
        verify_hashes(out/'source-package',f['native'])
        if read(out/'environment.json')['freeze_sha256']!=digest:raise ValueError('Wrong environment')
        for a in record.data['artifacts']:
            if sha(out/a['path'])!=a['sha256']:raise ValueError('Evidence changed: '+a['path'])
        totals['artifacts']+=len(record.data['artifacts'])
        attempt=record.data['attempts'][0];native=out/'attempts'/attempt['run_id']/'native'
        if attempt['run_id'] in identities:raise ValueError('Duplicate result identity')
        identities.add(attempt['run_id'])
        manifest=read(native/'input-manifest.json')
        if hashes(native/'input-snapshot')!=manifest['files']:raise ValueError('Input snapshot changed')
        version='sha256:'+hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if version!=attempt['input_version'] or version!=manifest['input_version']:raise ValueError('Wrong input identity')
        totals['input_snapshots']+=1;totals['solver_runs']+=1
        definition=batch/'definitions'/row['id'];declared=read(definition/'task.json');spec=read(definition/'spec.json')
        if declared!=record.data['task'] or declared['acceptance']!=s['acceptance']['criteria']:raise ValueError('Task contract changed')
        for name in hashes(definition/'reference'):
            if Path(name).parts[0] in ('0','constant','system') and canonical_dictionary(definition/'reference'/name)!=canonical_dictionary(out/attempt['input_snapshot']/name):raise ValueError('Task input differs')
        v=s['acceptance']['values']
        measured=solver_measurements((out/'attempts'/attempt['run_id']/'logs/log.simpleFoam').read_text(),attempt['execution']['exit_code'])
        if any(v[k]!=value for k,value in measured.items()):raise ValueError('Solver observation differs')
        if 'steady_evidence' in v:
            stability=stationary_fields(out/'case',spec,attempt)
            if stability!=read(out/'observations/field-stationarity.json'):raise ValueError('Stationarity differs')
            if v['steady_evidence']!=(v['steady_converged'] or stability['field_stationary'] is True):raise ValueError('Steady marker relabelled')
        if spec['kind']=='couette':
            actual=couette_inlet_order(out/'case',spec)
            saved=read(out/'observations/inlet-face-order.json')
            # Different Python runtimes may round face averages by a few ulps.
            # The physical face-order check itself retains its 1e-9 tolerance.
            if actual.keys()!=saved.keys():raise ValueError('Inlet order fields differ')
            for key,value in actual.items():
                other=saved[key]
                if isinstance(value,list):
                    if len(value)!=len(other):raise ValueError('Inlet order size differs')
                    for left,right in zip(value,other):
                        pairs=zip(left,right) if isinstance(left,list) else [(left,right)]
                        if any(abs(a-b)>1e-12 for a,b in pairs):raise ValueError('Inlet order differs')
                elif value!=other:raise ValueError('Inlet order differs')
        accepted=all(matches(v[c['id']],c) is True for c in declared['acceptance']) and s['acceptance']['error'] is None
        if accepted!=s['reference_validated'] or accepted!=s['completion']['success']:raise ValueError('Wrong quality claim')
        events=record.data['events'];claims=[(i,e) for i,e in enumerate(events) if e['kind']=='claim' and e.get('claim_type')=='completion']
        if len(claims)!=1:raise ValueError('Expected one completion')
        index,claim=claims[0]
        payload=read_verified_result(record,claim['source_artifact'],claim['run_id'],claim['input_version'])
        if json.loads(payload)['payload']['completion_claim']!=s['completion']:raise ValueError('Wrong registered completion source')
        measures=[(i,e) for i,e in enumerate(events) if e['kind']=='measurement']
        if len(measures)!=len(declared['acceptance']) or any(i>=index or e['value']!=v[e['criterion_id']] for i,e in measures):raise ValueError('Wrong measurement order/value')
        sampling=[e for e in events if e.get('operation')=='sample'];totals['sampling_calls']+=len(sampling)
        diagnosis=diagnose_sampling(record)
        if diagnosis!=read(out/'sampling-diagnostic.json'):raise ValueError('Sampling diagnostic differs')
        if accepted:
            last=sampling[-1];root=last.get('output_archive_root','observations/sampling')
            refs=reference_measurements(out/root/'profile.xy',out/root/'pressure.xy',spec)
            if any(abs(v[k]-value)>1e-12 for k,value in refs.items()):raise ValueError('Reference differs')
        elif s.get('scenario','healthy')!='healthy':
            if v['profile_error'] is not None or v['pressure_gradient_error'] is not None:raise ValueError('Failed sample published reference measures')
        if f.get('recovery_development'):
            if not s['development_contract_met']:raise ValueError('Recovery development contract failed')
            arm,scenario=s['arm'],s['scenario']
            expected=2 if arm=='candidate' and scenario!='healthy' else 1
            if len(sampling)!=expected or (expected==2 and sampling[1]['retry_of']!=sampling[0]['id']):raise ValueError('Retry budget/lineage differs')
            for e in events:
                if e.get('observation_type')=='explicit_sampling_challenge':
                    raw=read_verified_result(record,e['source_artifact'],e['run_id'],e['input_version']);intervention=json.loads(raw)
                    if intervention['natural_failure'] is not False or intervention['original_reader_bytes_unchanged'] is not True:raise ValueError('Challenge label/integrity missing')
                    data=read_verified_result(record,intervention['payload_artifact'],e['run_id'],e['input_version'])
                    if hashlib.sha256(data).hexdigest()!=intervention['removed_sha256']:raise ValueError('Removed sample bytes changed')
            if arm=='candidate' and scenario=='persistent_missing' and retries(record)['status']!='finding':raise ValueError('Persistent retry was disguised')
        rows.append(dict(id=row['id'],quality_met=accepted,contract_met=s.get('development_contract_met',accepted),
            original_simple_marker=v['steady_converged'],field_stationary=v.get('field_stationary'),
            source_artifacts_verified=len(record.data['artifacts']),sampling_diagnostic=diagnosis['summary'],
            retry_check=retries(record)['status'],sample_calls=len(sampling)))
    result=dict(freeze_sha256=digest,status=p['status'],offline_verification=True,totals=totals,rows=rows,
        new_model_calls=0,new_simulations=0,analysis_script_sha256=sha(Path(__file__)),optimization_effect_proven=False)
    write_json(batch/'archive-review.json',result)
    print(batch.name,json.dumps(totals),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--batch',type=Path,required=True);args=parser.parse_args()
    review(args.batch.resolve())


if __name__=='__main__':main()
