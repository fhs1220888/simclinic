"""Explicit task revision: reject old outputs, rerun, then consume new outputs."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.report import write_json
from simclinic.domains.openfoam.acceptance import channel_criteria, projected_rows, solver_measurements
from simclinic.checks.behavior import matches
from simclinic.foam_runtime import current_record
from simclinic.result_guard import read_verified_result

IMAGE='openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--full-run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    source=args.full_run.resolve();out=args.out.resolve()
    if out.exists():raise ValueError('Use a new demo directory.')
    summary=json.loads((source/'summary.json').read_text())
    if not summary['benchmark_accepted']:raise ValueError('Source full run must meet the original task.')
    old=summary['run_context']
    native=source/'source-package'
    manifest=json.loads((native/'candidate-manifest.json').read_text())
    for name,sha in manifest['source_files'].items():
        assert hashlib.sha256((native/name).read_bytes()).hexdigest()==sha,name
    sys.path.insert(0,str(native/'src'))
    from main import create_foam_agent_graph
    from config import Config
    from nodes.visualization_node import visualization_node
    from services import global_llm_service
    case=out/'case'
    # Copy identities and bytes together; imported old outputs retain old identities.
    shutil.copytree(source/'case',case)
    record_before=current_record(case,old)
    old_field=next(a for a in record_before.data['artifacts'] if re.fullmatch(r'[1-9][0-9]*/U',a['path']))
    read_verified_result(record_before,old_field['id'],old['run_id'],old['input_version'])
    inlet=case/'0/U';original=inlet.read_text()
    new_mean=0.02
    profile=' '.join(f'({6*new_mean*((i+0.5)/20)*(1-(i+0.5)/20):.12g} 0 0)' for i in range(20))
    changed,count=re.subn(r'value\s+nonuniform\s+List<vector>\s+20\s*\(.*?\)\s*;',
                         'value nonuniform List<vector> 20 ('+profile+');',original,flags=re.DOTALL)
    assert count==1
    changed,count=re.subn(r'internalField\s+uniform\s*\([^)]*\)\s*;',
                         'internalField uniform (0.02 0 0);',changed)
    assert count==1
    inlet.write_text(changed)
    task=dict(mean_velocity=new_mean,nu=0.001,length=1,height=0.1,width=0.01,mesh=[40,20,1],
        acceptance=channel_criteria(),basis='Explicit new demonstration task doubles the inlet profile; no solver or threshold changes.')
    write_json(out/'task-revision.json',task)
    # Record the mismatch without labelling the old field as the new result.
    write_json(out/'old-result-observation.json',dict(synthetic=False,fault_injection=True,
        old_context=old,producer_artifact=old_field,input_changed=True,
        description='Deliberately retained real old outputs while revising the inlet; no autonomous upstream stale-reference accident is claimed.'))
    config=Config(max_loop=0,max_time_limit=180)
    config.visualization_max_loop=0
    state=dict(case_dir=str(case),config=config,user_requirement='Render U as a PNG.',
               simclinic_context=old,requires_visualization=True,requires_hpc=False,error_logs=[],loop_count=0)
    previous=set((case/'.simclinic/native').rglob('render.stdout.log'))
    rejected=visualization_node(state)
    assert rejected['pyvista_visualization']['code']=='stale_input',rejected
    assert set((case/'.simclinic/native').rglob('render.stdout.log'))==previous
    os.environ['SIMCLINIC_DOCKER_IMAGE']=IMAGE
    graph=create_foam_agent_graph(entrypoint='local_runner').compile()
    result=graph.invoke(state,config={'recursion_limit':20})
    assert result['pyvista_visualization']['success'],result.get('visualization_summary')
    new=result['simclinic_context']
    assert new['run_id']!=old['run_id'] and new['input_version']!=old['input_version']
    stale=visualization_node({**result,'simclinic_context':old})
    assert stale['pyvista_visualization']['code']=='stale_run'
    record=current_record(case,new)
    new_field=next(a for a in record.data['artifacts'] if re.fullmatch(r'[1-9][0-9]*/U',a['path']))
    payload=read_verified_result(record,new_field['id'],new['run_id'],new['input_version'])
    argv=['docker','run','--rm','--pull','never','--platform','linux/amd64','--network','none',
          '--user','root','--mount',f'type=bind,src={case},dst=/case','--workdir','/case','--entrypoint','/bin/bash',IMAGE,
          '-c','source /opt/openfoam10/etc/bashrc && simpleFoam -postProcess -func sample -latestTime']
    sample=subprocess.run(argv,capture_output=True,timeout=120)
    (out/'sampling.stdout.log').write_bytes(sample.stdout);(out/'sampling.stderr.log').write_bytes(sample.stderr)
    assert sample.returncode==0
    # Select current time explicitly; old sampling output is retained as old evidence.
    latest=max(float(p.name) for p in case.iterdir() if p.is_dir() and re.fullmatch(r'[1-9][0-9]*',p.name))
    profile_path=next(p for p in (case/'postProcessing').rglob('profile.xy') if float(p.parent.name)==latest)
    pressure_path=next(p for p in (case/'postProcessing').rglob('pressure.xy') if float(p.parent.name)==latest)
    values=solver_measurements((case/'log.simpleFoam').read_text(),0)
    rows=projected_rows(profile_path,'y',('U_x','U_y','U_z'),4)
    assert len(rows)==20 and all(abs(row[0]-(0.0025+i*0.005))<1e-6 for i,row in enumerate(rows))
    expected=[6*new_mean*(row[0]/0.1)*(1-row[0]/0.1) for row in rows]
    values['profile_error']=math.sqrt(sum((r[1]-v)**2+r[2]**2+r[3]**2 for r,v in zip(rows,expected))/sum(v*v for v in expected))
    pressure=projected_rows(pressure_path,'x',('p',),2)
    assert len(pressure)==17 and all(abs(row[0]-(0.1+i*0.05))<1e-6 for i,row in enumerate(pressure))
    xm=sum(r[0] for r in pressure)/len(pressure);pm=sum(r[1] for r in pressure)/len(pressure)
    slope=sum((r[0]-xm)*(r[1]-pm) for r in pressure)/sum((r[0]-xm)**2 for r in pressure)
    gradient=-12*0.001*new_mean/(0.1**2)
    values['pressure_gradient_error']=abs(slope-gradient)/abs(gradient)
    accepted=all(matches(values[c['id']],c) is True for c in channel_criteria())
    assert accepted,values
    assert global_llm_service.service is None,'This regression must not call a model.'
    write_json(out/'summary.json',dict(synthetic=False,fault_injection=True,source_full_run=str(source),
        scope='Task revision and actual native solver/postprocessing regression, not a new full planner run or held-out optimization evaluation.',
        old_context=old,new_context=new,old_result_rejected_before_render=True,
        old_context_rejected_after_rerun=True,new_result_consumed=True,verified_bytes=len(payload),
        new_task_accepted=accepted,acceptance=values,model_calls=0,images=result['plot_outputs'],
        optimization_effect_proven=False))
    print('Inlet revision regression passed:',out)


if __name__=='__main__':main()
