"""Verify only finished runs of an active frozen batch; never edit primary data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import verify_hashes,hashes
from simclinic.checks.behavior import matches
from simclinic.domains.openfoam.input_contract import compare_case
from simclinic.domains.openfoam.reference_tasks import stationary_fields,reference_measurements,couette_inlet_order


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check(batch,out):
    frozen=read(batch/'freeze.json');progress=read(batch/'progress.json')
    digest=sha(batch/'freeze.json')
    if digest!=(batch/'freeze.sha256').read_text().strip():raise ValueError('Freeze changed')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('corpus','corpus'),('definitions','definitions')):
        verify_hashes(batch/folder,frozen[key])
    for name in ('simclinic/domains/openfoam/input_contract.py','simclinic/domains/openfoam/reference_tasks.py'):
        if sha(ROOT/name)!=sha(batch/'common-runtime'/name):raise ValueError('Reader changed')
    rows=[r for r in progress['runs'] if r['status']=='finished']
    if [r['id'] for r in rows]!=frozen['schedule'][:len(rows)]:raise ValueError('Not a completed fixed prefix')
    totals=dict(artifacts=0,solver_runs=0,input_snapshots=0,model_calls=0,model_tokens=0,sampler_calls=0)
    identities=set()
    for row,entry in zip(rows,frozen['entries']):
        run=batch/'runs'/row['id'];record=load_record(run/'record.json',run);summary=read(run/'summary.json')
        verify_hashes(run/'source-package',frozen['native'])
        if record.data['task']!=read(batch/'definitions'/row['id']/'task.json'):raise ValueError('Task changed')
        for a in record.data['artifacts']:
            if sha(run/a['path'])!=a['sha256']:raise ValueError('Artifact changed')
        totals['artifacts']+=len(record.data['artifacts'])
        for context in record.data['attempts']:
            if context['run_id'] in identities:raise ValueError('Reused identity')
            identities.add(context['run_id'])
            native=run/'attempts'/context['run_id']/'native';manifest=read(native/'input-manifest.json')
            if hashes(native/'input-snapshot')!=manifest['files']:raise ValueError('Input bytes differ')
            version='sha256:'+hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if version!=context['input_version'] or version!=manifest['input_version']:raise ValueError('Input version differs')
            totals['solver_runs']+=1;totals['input_snapshots']+=1
        meta=[read(p) for p in sorted((run/'model-calls').glob('*/metadata.json'))]
        if any(m['outcome']!='pass' or m['total_tokens'] is None for m in meta):raise ValueError('Failed/unknown model call in checkpoint')
        tokens=sum(m['total_tokens'] for m in meta)
        if tokens!=summary['model_tokens'] or tokens!=record.data['metrics']['model_tokens']:raise ValueError('Usage differs')
        totals['model_calls']+=len(meta);totals['model_tokens']+=tokens
        samples=[e for e in record.data['events'] if e.get('operation')=='sample']
        totals['sampler_calls']+=len(samples)
        context=record.data['attempts'][-1];assessment=summary['acceptance'];values=assessment['values']
        checks,_=compare_case(run/context['input_snapshot'],batch/'definitions'/row['id']/'reference',entry['spec'])
        if checks!=assessment['input_checks']:raise ValueError('Input decisions differ')
        accepted=assessment['error'] is None and all(matches(values[c['id']],c) is True for c in record.data['task']['acceptance'])
        if accepted!=summary['quality_met'] or summary['completion']['success']!=accepted:raise ValueError('Quality differs')
        if values['execution_end']:
            stability=stationary_fields(run/'case',entry['spec'],context)
            if stability!=read(run/'observations/field-stationarity.json'):raise ValueError('Stationarity differs')
            if couette_inlet_order(run/'case',entry['spec'])!=read(run/'observations/inlet-face-order.json'):raise ValueError('Inlet order differs')
        if samples and samples[-1]['outcome']=='pass':
            folder=run/samples[-1].get('output_archive_root','observations/sampling')
            measured=reference_measurements(folder/'profile.xy',folder/'pressure.xy',entry['spec'])
            if any(abs(values[k]-v)>1e-12 for k,v in measured.items()):raise ValueError('Reference differs')
        elif values['profile_error'] is not None or values['pressure_gradient_error'] is not None:
            raise ValueError('Failed sampler manufactured reference evidence')
    write_json(out,dict(freeze_sha256=digest,finished_runs=len(rows),run_ids=[r['id'] for r in rows],totals=totals,
                        new_model_calls=0,new_simulations=0,primary_results_preserved=True,checker_sha256=sha(Path(__file__))))
    print(json.dumps(dict(finished_runs=len(rows),totals=totals)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();check(args.batch.resolve(),args.out.resolve())
