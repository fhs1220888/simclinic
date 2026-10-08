"""Observed execution preserves pre-run inputs and explicit mesh gate decisions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from .core.report import write_json
from .foam_execution import validate_allrun
from .foam_runtime import inputs
from .collection import digest


def run_container(script_path,out_file,err_file,working_dir,max_time_limit):
    case=Path(working_dir).resolve()
    if Path(script_path).resolve()!=case/'Allrun':raise ValueError('Only case Allrun is supported.')
    validate_allrun(Path(script_path).read_text())
    context=json.loads((case/'.simclinic/native/current.json').read_text())
    manifest=inputs(case)
    if context['input_version']!='sha256:'+digest(manifest):raise ValueError('Run inputs changed before execution.')
    directory=case/'.simclinic/native'/context['run_id']
    snapshot=directory/'input-snapshot'
    snapshot.mkdir()
    for relative in manifest:
        target=snapshot/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(case/relative,target)
    write_json(directory/'input-manifest.json',dict(**{k:context[k] for k in ('run_id','input_version')},files=manifest))
    image=os.environ['SIMCLINIC_DOCKER_IMAGE']
    if '@sha256:' not in image:raise ValueError('An immutable image digest is required.')
    helper=Path(__file__).with_name('openfoam_observer.py')
    name='simclinic-session-'+context['run_id']
    functions='\n'.join(command+'() { python3 /observer.py '+command+' "$@"; }; export -f '+command
                        for command in ('blockMesh','checkMesh','simpleFoam'))
    command='source /opt/openfoam10/etc/bashrc\n'+functions+'\nexec bash /case/Allrun'
    argv=['docker','run','--rm','--pull','never','--name',name,'--init','--platform','linux/amd64',
          '--network','none','--cpus','2','--memory','2g','--read-only','--tmpfs','/tmp','--user','root',
          '--mount',f'type=bind,src={case},dst=/case','--mount',f'type=bind,src={helper},dst=/observer.py,readonly',
          '--workdir','/case','--entrypoint','/bin/bash',image,'-c',command]
    started=time.monotonic();timed_out=False
    try:
        process=subprocess.run(argv,capture_output=True,timeout=max_time_limit)
        code=process.returncode;stdout=process.stdout;stderr=process.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out=True;code=124;stdout=exc.stdout or b'';stderr=exc.stderr or b''
    finally:
        subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    Path(out_file).write_bytes(stdout);Path(err_file).write_bytes(stderr)
    status=dict(exit_code=code,timed_out=timed_out,elapsed_seconds=time.monotonic()-started,image=image,
        policy_validated=True,command_order=['blockMesh','checkMesh','simpleFoam'],
        observer_sha256=__import__('hashlib').sha256(helper.read_bytes()).hexdigest())
    write_json(directory/'execution.json',status)
    return status
