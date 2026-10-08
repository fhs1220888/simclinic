"""Exercise registered upstream tool functions directly; start no MCP server."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
NATIVE=ROOT/'integration/foam_agent_v210/native/patched'
sys.path[:0]=[str(NATIVE),str(NATIVE/'src'),str(ROOT)]
from simclinic.foam_runtime import verified_workspace
from simclinic.core.report import write_json
from simclinic.domains.openfoam.channel import create_channel
from src.mcp.fastmcp_server import mcp, RunSimulationRequest, VisualizationRequest, global_config
from services.visualization import run_pyvista_script
from services import global_llm_service
from run_native_postprocessing import IMAGE


class ConsoleContext:
    """Only logging calls used by the real registered tool functions."""
    async def info(self,message):
        print(message)
    async def error(self,message):
        print(message)


async def test(out, graph_run):
    if out.exists():
        raise ValueError('Use a new entrypoint output directory.')
    manifest=json.loads((NATIVE/'candidate-manifest.json').read_text())
    for name,sha in manifest['source_files'].items():
        assert hashlib.sha256((NATIVE/name).read_bytes()).hexdigest()==sha
    global_config.max_loop=0
    tools=await mcp.get_tools()
    ctx=ConsoleContext()
    case=create_channel(out/'case')
    (case/'Allrun').write_bytes((graph_run/'case/Allrun').read_bytes())
    info=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    bridge=out/'host-bridge'
    (bridge/'etc').mkdir(parents=True)
    docker=['docker','run','--rm','--pull','never','--platform','linux/amd64','--network','none','--cpus','2','--memory','2g','--user','root','--mount',f'type=bind,src={case},dst=/case','--workdir','/case','--entrypoint','/bin/bash',info['Id']]
    lines=[]
    for command in ('blockMesh','checkMesh','simpleFoam','postProcess'):
        argv=docker+['-c','source /opt/openfoam10/etc/bashrc && '+command+' "$@"','simclinic']
        lines.extend([command+'() { '+shlex.join(argv)+' "$@"; }','export -f '+command])
    (bridge/'etc/bashrc').write_text('\n'.join(lines)+'\n')
    os.environ['WM_PROJECT_DIR']=str(bridge)
    execution=await tools['run'].fn(RunSimulationRequest(case_dir=str(case),timeout=120),ctx)
    assert execution.status=='success' and not execution.errors
    rendered=await tools['visualization'].fn(VisualizationRequest(case_dir=str(case),quantity='velocity U',expected_run_id=execution.run_id,expected_input_version=execution.input_version),ctx)
    assert len(rendered.artifacts)==1
    write_json(out/'tool-run.json',execution.model_dump())
    write_json(out/'tool-visualization.json',rendered.model_dump())
    before=set((case/'.simclinic/native').rglob('render.stdout.log'))
    try:
        await tools['visualization'].fn(VisualizationRequest(case_dir=str(case),quantity='velocity',expected_run_id='0'*32,expected_input_version=execution.input_version),ctx)
        raise AssertionError('Tool must reject old run')
    except ValueError as exc:
        assert 'stale_run' in str(exc), str(exc)
    assert before==set((case/'.simclinic/native').rglob('render.stdout.log'))
    # Regression: a zero-exit script must not reuse a preexisting image.
    graph=json.loads((graph_run/'summary.json').read_text())
    with verified_workspace(graph_run/'case',graph['run_context']) as work:
        old_image=work/'visualization.png'
        old_image.write_bytes(Path(graph['image']).read_bytes())
        ok,image,errors=run_pyvista_script(str(work),'pass\n',expected_png='visualization.png')
        assert not ok and not image and errors
        assert not old_image.exists()
    assert global_llm_service.service is None
    write_json(out/'summary.json',dict(synthetic=False,registered_tool_functions_called=True,protocol_transport_tested=False,
        run_response_includes_context=True,visualization_generated=rendered.artifacts[0],stale_run_rejected=True,
        zero_exit_without_new_png_rejected=True,model_calls=0,mcp_server_started=False,image_digest=IMAGE,
        simclinic_runtime_sha256=hashlib.sha256((ROOT/'simclinic/foam_runtime.py').read_bytes()).hexdigest()))
    print('Native tool entrypoints and stale PNG regression verified:',out)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--graph-run',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(test(args.out.resolve(),args.graph_run.resolve()))
