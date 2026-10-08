"""Read-only archive review and authored offline matrix; no model/solver calls."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import hashes, verify_hashes
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.domains.openfoam.input_contract import FILES, POLICY, compare_input
from simclinic.evaluation.input_contract_validation import positive_text, apply_negative


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_stopped(batch):
    frozen = read(batch/'freeze.json'); progress = read(batch/'progress.json')
    if sha(batch/'freeze.json')!=(batch/'freeze.sha256').read_text().strip():
        raise ValueError('Stopped freeze changed')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):
        verify_hashes(batch/folder,frozen[key])
    if progress['status']!='reference_contract_failed' or len(progress['runs'])!=2:
        raise ValueError('Expected original two-run stopped prefix')
    if [r['id'] for r in progress['runs']]!=frozen['schedule'][:2] or [r['exit_code'] for r in progress['runs']]!=[0,2]:
        raise ValueError('Stopped schedule changed')
    total = dict(artifacts=0, input_snapshots=0, solver_runs=0, sampler_calls=0, model_calls=0)
    ids = set()
    for index,row in enumerate(progress['runs']):
        out=batch/'runs'/row['id']; record=load_record(out/'record.json',out); summary=read(out/'summary.json')
        verify_hashes(out/'source-package',frozen['native'])
        if record.data['model_calls'] or record.data['metrics']['model_tokens']!=0 or len(record.data['attempts'])!=1:
            raise ValueError('Unexpected model/retry state')
        for artifact in record.data['artifacts']:
            if sha(out/artifact['path'])!=artifact['sha256']: raise ValueError('Stopped artifact changed')
        context=record.data['attempts'][0]
        if context['run_id'] in ids: raise ValueError('Reused identity')
        ids.add(context['run_id'])
        native=out/'attempts'/context['run_id']/'native'
        manifest=read(native/'input-manifest.json')
        if hashes(native/'input-snapshot')!=manifest['files'] or hashes(native/'input-snapshot')!=hashes(batch/'definitions'/row['id']/'inputs'):
            raise ValueError('Stopped input snapshot changed')
        version='sha256:'+hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if version!=manifest['input_version'] or version!=context['input_version']: raise ValueError('Stopped input identity differs')
        # Execute only the archived literal reader, in a fresh Python process,
        # to preserve its original v2 result; never regrade the stopped batch.
        snippet = """import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from simclinic.domains.openfoam.input_contract import compare_case
out=Path(sys.argv[2]); definition=Path(sys.argv[3])
record=json.loads((out/'record.json').read_text()); a=record['attempts'][0]
checks,evidence=compare_case(out/a['input_snapshot'],definition/'reference',json.loads((definition/'spec.json').read_text()))
summary=json.loads((out/'summary.json').read_text())
assert checks==summary['acceptance']['input_checks'] and evidence==summary['quantity_comparison']
print('original_frozen_literal_decisions_verified')
"""
        subprocess.run([sys.executable,'-c',snippet,str(batch/'common-runtime'),str(out),str(batch/'definitions'/row['id'])],check=True,capture_output=True,text=True)
        log=(out/'attempts'/context['run_id']/'logs/log.simpleFoam').read_text()
        values=summary['acceptance']['values']
        observations=solver_measurements(log,context['execution']['exit_code'])
        if any(values[k]!=value for k,value in observations.items()): raise ValueError('Stopped solver observation changed')
        if index==0:
            if not summary['development_contract_met'] or not summary['completion']['success']: raise ValueError('First control differs')
        elif summary['development_contract_met'] or summary['completion']['success'] or observations['execution_end'] or 'punctuation token' not in log or "'+'" not in log:
            raise ValueError('Original leading-plus failure differs')
        total['artifacts']+=len(record.data['artifacts'])
        total['input_snapshots']+=1; total['solver_runs']+=1
        total['sampler_calls']+=sum(e.get('operation')=='sample' for e in record.data['events'])
    result=dict(freeze_sha256=sha(batch/'freeze.json'), primary_results_sha256=sha(batch/'results.json'),
                totals=total, prefix_verified=True, original_results_preserved=True,
                new_model_calls=0, new_simulations=0, reason='Foundation10_rejects_leading_plus_scalar')
    write_json(batch/'stopped-prefix-verification.json',result)
    return result


def matrix(batch):
    frozen=read(batch/'freeze.json')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):
        verify_hashes(batch/folder,frozen[key])
    if frozen['input_matching_policy']!=POLICY: raise ValueError('Unexpected input policy')
    for relative in ('domains/openfoam/input_contract.py','evaluation/input_contract_validation.py'):
        if sha(ROOT/'simclinic'/relative)!=sha(batch/'common-runtime/simclinic'/relative):
            raise ValueError('Review reader/control author differs from frozen validation')
    entry=frozen['entries'][0]; definition=batch/'definitions'/entry['id']
    base=batch/'offline-input-matrix'
    if base.exists():
        manifest=read(batch/'offline-input-matrix-manifest.json')
        verify_hashes(base,manifest['files'])
        if manifest['checker_sha256']!=sha(ROOT/'simclinic/domains/openfoam/input_contract.py'):
            raise ValueError('Offline checker changed')
        result=read(base/'results.json')
        if len(result['rows'])!=45 or result['checks']!=45 or result['matched']!=45:
            raise ValueError('Offline matrix coverage differs')
        for row in result['rows']:
            evidence=row['evidence']
            replay=compare_input(evidence['actual'],evidence['expected'],row['relative_path'],entry['spec'])
            if replay!=evidence or replay['status']!=row['expected_status']:
                raise ValueError('Offline matrix replay differs')
        return result
    base.mkdir()
    rows=[]
    for name in FILES:
        reference=definition/'reference'/name
        for variant,expected_status in (('positive','pass'),('negative','finding'),('missing','insufficient_evidence'),
                                       ('directive','insufficient_evidence'),('duplicate','insufficient_evidence')):
            case=base/name.replace('/','-')/variant
            p=case/name; p.parent.mkdir(parents=True)
            if variant!='missing':
                p.write_text(reference.read_text())
                if variant=='positive': p.write_text(positive_text(p.read_text(),name))
                elif variant=='negative': apply_negative(case,name)
                elif variant=='directive': p.write_text(p.read_text()+'\n#include "unavailable-override"\n')
                elif variant=='duplicate': p.write_text(p.read_text()+'\nFoamFile { version 2.0; format ascii; class dictionary; object duplicate; }\n')
            evidence=compare_input(p,reference,name,entry['spec'])
            if evidence['status']!=expected_status: raise ValueError('Offline matrix differs')
            rows.append(dict(relative_path=name,variant=variant,expected_status=expected_status,evidence=evidence))
    result=dict(policy=POLICY, authored_development=True, synthetic_inputs=True, heldout=False,
                new_model_calls=0,new_simulations=0,checks=len(rows), matched=len(rows),rows=rows)
    write_json(base/'results.json',result)
    # Keep the manifest outside the tree it hashes; otherwise it becomes an
    # unregistered extra file when verifying the exact file set.
    write_json(batch/'offline-input-matrix-manifest.json',dict(files=hashes(base), checker_sha256=sha(ROOT/'simclinic/domains/openfoam/input_contract.py')))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--stopped',type=Path,required=True)
    args=parser.parse_args(); batch=args.batch.resolve(); stopped=args.stopped.resolve()
    verification=read(batch/'archive-verification.json')
    if not verification['all_expected_controls_verified']: raise ValueError('Verify complete real batch first')
    stopped_result=review_stopped(stopped)
    offline=matrix(batch)
    write_json(batch/'development-review.json',dict(
        full_batch=verification, stopped_prefix=stopped_result, offline_matrix_checks=offline['checks'],
        reviewer_sha256=sha(Path(__file__)), new_model_calls=0,new_simulations=0,
        historical_scores_preserved=True,optimization_effect_proven=False))
    print(json.dumps(dict(real_complete=verification['totals'], stopped=stopped_result['totals'], offline_checks=offline['checks'])))


if __name__=='__main__': main()
