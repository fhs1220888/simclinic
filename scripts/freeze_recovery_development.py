"""Freeze explicit recovery controls using the validated Couette mechanism only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_full_agent import IMAGE,CORPUS
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import hashes,verify_hashes
from simclinic.domains.openfoam.reference_tasks import DEVELOPMENT_TASKS,create_reference,task


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve()
    if out.exists():raise ValueError('Use fresh development batch')
    reference_batch=ROOT/'real-runs/new-reference-development-v2-20261005'
    rf=json.loads((reference_batch/'freeze.json').read_text())
    verify_hashes(reference_batch/'common-runtime',rf['common_runtime'])
    for rid in ('C0-coarse','C0-fine'):
        summary=json.loads((reference_batch/'runs'/rid/'summary.json').read_text())
        if not summary['reference_validated']:raise ValueError('Couette references must pass both meshes')
    native=ROOT/'integration/foam_agent_v210/native/content'
    manifest=json.loads((native/'candidate-manifest.json').read_text())
    for name,expected in manifest['source_files'].items():
        if sha(native/name)!=expected:raise ValueError('Native source changed')
    if sha(ROOT/'simclinic/input_content.py')!=manifest['runtime_requirements']['input_content_sha256']:raise ValueError('Input helper changed')
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    out.mkdir(parents=True)
    common=out/'common-runtime'
    shutil.copytree(ROOT/'simclinic',common/'simclinic',ignore=shutil.ignore_patterns('__pycache__'))
    (common/'scripts').mkdir()
    for name in ('validate_reference_tasks.py','freeze_recovery_development.py'):
        shutil.copy2(ROOT/'scripts'/name,common/'scripts'/name)
    shutil.copytree(native,out/'native',ignore=shutil.ignore_patterns('__pycache__'))
    entries=[]
    for factor,label in ((1,'coarse'),(2,'fine')):
        spec=dict(DEVELOPMENT_TASKS[0],nx=40*factor,ny=20*factor)
        for i,scenario in enumerate(('healthy','transient_missing','persistent_missing')):
            for arm in (('baseline','candidate') if i%2==0 else ('candidate','baseline')):
                rid='C0-'+label+'-'+scenario+'-'+arm
                folder=out/'definitions'/rid
                create_reference(CORPUS/'reference-case',folder/'reference',spec)
                write_json(folder/'spec.json',spec);write_json(folder/'task.json',task(spec))
                entries.append(dict(id=rid,scenario=scenario,arm=arm))
    f=dict(experiment='bounded-sampling-recovery-development-v1',frozen_at=datetime.now(timezone.utc).isoformat(),
        recovery_development=True,entries=entries,schedule=[e['id'] for e in entries],heldout=False,new_model_calls=0,
        image=IMAGE,image_id=image['Id'],python=sys.version,executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines(),
        common_runtime=hashes(common),native=hashes(out/'native'),definitions=hashes(out/'definitions'),
        resources=dict(cpus=2,memory='2g',platform='linux/amd64',network='none'),
        idle_sleep_guard=dict(command='/usr/bin/caffeinate',sha256=sha('/usr/bin/caffeinate')),
        policy_difference=dict(baseline='ReferenceTaskCompletion',candidate='RecoveringReferenceCompletion, one new verified sampling workspace only for typed missing output'),
        identical_upstream_and_challenge=True,model_calls_forbidden=True,solver_retries_forbidden=True,
        physical_parameters_and_acceptance_changed=False,previous_failed_runs_preserved=True,
        reference_basis=dict(batch=str(reference_batch),freeze_sha256=sha(reference_batch/'freeze.json'),
                             excluded='F0 fine not stationary; not eligible for recovery effectiveness comparison'),
        limits='Couette only, two mesh resolutions, explicit injected loss; not new heldout, natural recovery frequency, planner compatibility or overall optimization',
        stopping='Exactly 12 fresh solver/render runs; stop unexpected contract/setup failure; expected honest task failures are valid observations',
        optimization_effect_proven=False)
    write_json(out/'freeze.json',f);(out/'freeze.sha256').write_text(sha(out/'freeze.json')+'\n')
    print('Frozen 12 zero-model recovery controls:',out,flush=True)


if __name__=='__main__':main()
