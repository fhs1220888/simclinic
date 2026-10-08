"""Run the native graph tail with real Docker CFD and local PyVista rendering.

No node stubs, AST extraction, model calls, credential reads, or new MCP server.
The source package is imported normally; the real graph starts at local_runner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NATIVE = ROOT / 'integration/foam_agent_v210/native/patched'
IMAGE = 'openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise ValueError('Use a new output directory.')
    manifest = json.loads((NATIVE/'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        assert hashlib.sha256((NATIVE/name).read_bytes()).hexdigest() == sha, name
    info=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    sys.path.insert(0,str(NATIVE/'src'))
    from main import create_foam_agent_graph
    from config import Config
    from nodes.visualization_node import visualization_node
    from services import global_llm_service
    from simclinic.domains.openfoam.channel import create_channel
    from simclinic.domains.openfoam.acceptance import channel_criteria, solver_measurements, profile_error, pressure_gradient_error
    from simclinic.checks.behavior import matches
    from simclinic.core.report import write_json
    from simclinic.core.record import load_record
    from simclinic.core.report import write_report
    from simclinic.cli import get_report

    case = create_channel(out/'case')
    (case/'Allrun').write_text('#!/bin/bash\nset -eu\nblockMesh > log.blockMesh 2>&1\ncheckMesh > log.checkMesh 2>&1\nsimpleFoam > log.simpleFoam 2>&1\nsimpleFoam -postProcess -func sample -latestTime > log.sample 2>&1\n')
    bridge=out/'host-bridge'
    (bridge/'etc').mkdir(parents=True)
    docker=['docker','run','--rm','--pull','never','--platform','linux/amd64','--network','none','--cpus','2','--memory','2g','--user','root','--mount',f'type=bind,src={case},dst=/case','--workdir','/case','--entrypoint','/bin/bash',info['Id']]
    lines=[]
    for command in ('blockMesh','checkMesh','simpleFoam','postProcess'):
        argv=docker+['-c','source /opt/openfoam10/etc/bashrc && '+command+' "$@"','simclinic']
        lines.append(command+'() { '+shlex.join(argv)+' "$@"; }')
        lines.append('export -f '+command)
    (bridge/'etc/bashrc').write_text('\n'.join(lines)+'\n')
    os.environ['WM_PROJECT_DIR']=str(bridge)
    os.environ['LANGCHAIN_TRACING_V2']='false'
    config=Config(max_loop=0,max_time_limit=120)
    graph=create_foam_agent_graph(entrypoint='local_runner').compile()
    initial={'case_dir':str(case),'config':config,'user_requirement':'Render the velocity U field as a PNG.',
             'requires_visualization':True,'requires_hpc':False,'error_logs':[], 'loop_count':0}
    result=graph.invoke(initial,config={'recursion_limit':20})
    assert result.get('pyvista_visualization',{}).get('success'), result.get('visualization_summary')
    assert len(result['plot_outputs'])==1
    current=result['simclinic_context']
    values=solver_measurements((case/'log.simpleFoam').read_text(),0)
    values['profile_error']=profile_error(next((case/'postProcessing').rglob('profile.xy')))
    values['pressure_gradient_error']=pressure_gradient_error(next((case/'postProcessing').rglob('pressure.xy')))
    acceptance=dict(values=values,accepted=all(matches(values[c['id']],c) is True for c in channel_criteria()))
    assert acceptance['accepted'], acceptance
    write_json(out/'graph-result.json', {key:value for key,value in result.items() if key not in ('config','llm_service')})
    write_json(out/'acceptance.json',acceptance)
    write_report(get_report(load_record(case/'.simclinic/native'/current['run_id']/'record.json',case)),out/'audit')
    attempts=[]
    def check(name, context, expected):
        previous=set((case/'.simclinic/native').rglob('render.stdout.log'))
        state=visualization_node({**result,'simclinic_context':context})
        assert not state['pyvista_visualization']['success'] and state['plot_outputs']==[], state
        actual=state['pyvista_visualization']['code']
        assert actual==expected, (name,actual,expected)
        assert previous==set((case/'.simclinic/native').rglob('render.stdout.log'))
        attempts.append(dict(name=name,allowed=False,code=actual,render_started=False))
    check('old_run',dict(current,run_id='0'*32),'stale_run')
    check('old_input',dict(current,input_version='sha256:other'),'stale_input')
    check('unknown_context',None,'unknown_expected_identity')
    inlet=case/'0/U'
    original=inlet.read_bytes()
    inlet.write_bytes(original.replace(b'0.01',b'0.02'))
    check('actual_inlet_file_change',current,'stale_input')
    inlet.write_bytes(original)
    field=sorted([path for path in case.iterdir() if path.name.isdigit() and path.name!='0'])[0]/'U'
    originalfield=field.read_bytes()
    field.write_bytes(originalfield+b'\n// deliberate modification\n')
    check('solver_field_changed',current,'digest_mismatch')
    field.write_bytes(originalfield)
    # Same native graph, real nonzero process; cached visualization must not leak.
    failure_case=create_channel(out/'failed-case')
    (failure_case/'Allrun').write_text('#!/bin/bash\nexit 7\n')
    failed=graph.invoke({**result,'case_dir':str(failure_case),'config':config},config={'recursion_limit':20})
    assert failed['error_logs'] and not failed.get('plot_outputs')
    assert failed.get('termination_reason')=='max_review_loop_reached'
    assert global_llm_service.service is None, 'Unexpected model initialization'
    write_json(out/'failed-graph.json',{key:value for key,value in failed.items() if key not in ('config','llm_service')})
    # Preserve inputs for that failed run; do not relabel or revive old results.
    write_json(out/'environment.json',dict(python=sys.version,executable=sys.executable,image_id=info['Id'],image_digest=IMAGE,
        simclinic_runtime_sha256=hashlib.sha256((ROOT/'simclinic/foam_runtime.py').read_bytes()).hexdigest(),
        requirements_sha256=hashlib.sha256((NATIVE.parent/'requirements.lock').read_bytes()).hexdigest(),
        architecture='CFD linux/amd64; native graph and renderer Darwin arm64',source_manifest=manifest))
    write_json(out/'summary.json',dict(synthetic=False,native_imports=True,graph_entrypoint='local_runner',
        nodes_exercised=['local_runner','visualization','reviewer'], native_routes_exercised=['route_after_runner','route_after_reviewer'],
        graph_postprocessing_success=True,image=result['plot_outputs'][0],run_context=current,
        acceptance=acceptance,rejections=attempts,failed_execution_blocked=True,
        model_calls=0,full_model_driven_workflow=False,mcp_server_started=False,
        optimization_effect_proven=False))
    print('Native graph postprocessing integration verified:',out)


if __name__=='__main__':
    main()
