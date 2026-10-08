"""Read-only status for the fixed native-source v3 comparison schema."""
import argparse
import json
from pathlib import Path
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.evaluation.heldout import verify_hashes


def read(p):return json.loads(Path(p).read_text())


def status(batch):
    f=read(batch/'freeze.json')
    if hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest()!=(batch/'freeze.sha256').read_text().strip():
        raise ValueError('Freeze changed')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('corpus','corpus'),('definitions','definitions')):
        verify_hashes(batch/folder,f[key])
    p=read(batch/'progress.json') if (batch/'progress.json').exists() else dict(status='frozen_not_started',runs=[],known_model_tokens=0)
    entries={e['id']:e for e in f['entries']}
    if [r['id'] for r in p['runs']]!=f['schedule'][:len(p['runs'])]:raise ValueError('Run order differs')
    finished=[r for r in p['runs'] if r['status']=='finished']
    active=[];active_known=0;unknown=sum(r.get('model_tokens') is None for r in finished)
    for row in p['runs']:
        if row['status']!='running':continue
        meta=[read(m) for m in sorted((batch/'runs'/row['id']/'model-calls').glob('*/metadata.json'))]
        known=sum(m['total_tokens'] for m in meta if m.get('total_tokens') is not None)
        pending=sum(m.get('total_tokens') is None for m in meta)
        active_known+=known;unknown+=pending
        active.append(dict(id=row['id'],stage=entries[row['id']]['stage'],model_call_records=len(meta),
                           completed_model_calls=sum(m['outcome']=='pass' for m in meta),known_model_tokens=known,
                           calls_with_pending_or_unknown_usage=pending))
    counts={}
    for stage in ('development','heldout'):
        rows=[r for r in finished if entries[r['id']]['stage']==stage]
        counts[stage]=dict(planned=sum(e['stage']==stage for e in f['entries']),finished=len(rows),quality_pass=sum(r.get('quality_met') is True for r in rows))
    lower=p['known_model_tokens']+active_known
    return dict(status=p['status'],counts=counts,active=active,observed_usage_lower_bound=lower,
                observed_model_tokens=None if unknown else lower,read_only=True,new_model_calls=0,new_simulations=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--batch',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(status(args.batch.resolve()),ensure_ascii=False))
