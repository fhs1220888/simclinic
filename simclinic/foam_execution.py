"""Execute generated Allrun only inside a pinned, disconnected container."""
import json
import os
from pathlib import Path
import subprocess
import time
import re
from .core.report import write_json


def validate_allrun(text):
    """The initial benchmark executes three observable commands, in order."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not lines or lines.pop(0) != 'set -eu':
        raise ValueError('Allrun must begin with set -eu.')
    expected = ('blockMesh', 'checkMesh', 'simpleFoam')
    if len(lines) != len(expected):
        raise ValueError('Allrun must contain exactly the three benchmark commands.')
    for line, command in zip(lines, expected):
        if not re.fullmatch(command+r'\s*>\s*log\.'+command+r'\s+2>&1', line):
            raise ValueError('Unsupported Allrun statement; benchmark command coverage would be unknown.')


def run_container(script_path,out_file,err_file,working_dir,max_time_limit):
    case=Path(working_dir).resolve()
    if Path(script_path).resolve()!=case/'Allrun':raise ValueError('Only case Allrun is supported.')
    validate_allrun(Path(script_path).read_text())
    context=json.loads((case/'.simclinic/native/current.json').read_text())
    image=os.environ['SIMCLINIC_DOCKER_IMAGE']
    if '@sha256:' not in image:raise ValueError('An immutable solver image digest is required.')
    name='simclinic-full-'+context['run_id']
    helper=Path(__file__).resolve().parents[1]/'integration/foam_agent_v210/native/foam_tool.py'
    functions='\n'.join(command+'() { python3 /foam_tool.py '+command+' "$@"; }; export -f '+command for command in ('blockMesh','checkMesh','simpleFoam','foamDictionary'))
    command='source /opt/openfoam10/etc/bashrc\n'+functions+'\nexec bash /case/Allrun'
    argv=['docker','run','--rm','--pull','never','--name',name,'--init','--platform','linux/amd64',
          '--network','none','--cpus','2','--memory','2g','--read-only','--tmpfs','/tmp',
          '--user','root','--mount',f'type=bind,src={case},dst=/case',
          '--mount',f'type=bind,src={helper},dst=/foam_tool.py,readonly',
          '--workdir','/case','--entrypoint','/bin/bash',image,'-c',command]
    started=time.monotonic()
    timed_out=False
    try:
        result=subprocess.run(argv,capture_output=True,timeout=max_time_limit)
        code=result.returncode;stdout=result.stdout;stderr=result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out=True;code=124;stdout=exc.stdout or b'';stderr=exc.stderr or b''
    finally:
        subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    Path(out_file).write_bytes(stdout);Path(err_file).write_bytes(stderr)
    status=dict(exit_code=code,timed_out=timed_out,elapsed_seconds=time.monotonic()-started,
                image=image,network='none',scope='generated Allrun in isolated container')
    write_json(case/'.simclinic/native'/context['run_id']/'execution.json',status)
    return status
