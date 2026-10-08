"""Standalone in-container command observer for the bounded serial workflow."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


def main():
    case=Path('/case')
    operation=sys.argv[1]
    if operation not in {'blockMesh','checkMesh','simpleFoam'}:
        raise ValueError('Unsupported observed command')
    context=json.loads((case/'.simclinic/native/current.json').read_text())
    call_id=uuid.uuid4().hex
    directory=case/'.simclinic/native'/context['run_id']/'command-calls'/call_id
    directory.mkdir(parents=True)
    manifest={}
    for folder in ('0','constant','system'):
        for path in sorted((case/folder).rglob('*')):
            relative=path.relative_to(case)
            if path.is_file() and not path.is_symlink() and relative.parts[:2]!=('constant','polyMesh'):
                manifest[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest['Allrun']=hashlib.sha256((case/'Allrun').read_bytes()).hexdigest()
    fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    started=time.monotonic()
    metadata=dict(id=call_id,operation=operation,argv=sys.argv[2:],
        phase='preflight' if operation=='checkMesh' else 'solve' if operation=='simpleFoam' else 'other',
        run_id=context['run_id'],input_version='sha256:'+fingerprint,input_fingerprint=fingerprint,
        started=started,exit_code=None,outcome='unknown',elapsed_seconds=None)
    (directory/'started.json').write_text(json.dumps(metadata,indent=2)+'\n')
    result=subprocess.run([shutil.which(operation)]+sys.argv[2:],capture_output=True)
    (directory/'stdout.log').write_bytes(result.stdout)
    (directory/'stderr.log').write_bytes(result.stderr)
    text=(result.stdout+result.stderr).decode(errors='replace')
    outcome='fail' if result.returncode else 'pass' if operation!='checkMesh' or 'Mesh OK.' in text else 'unknown'
    effective_code=result.returncode if outcome!='unknown' else 65
    metadata.update(exit_code=result.returncode,outcome=outcome,elapsed_seconds=time.monotonic()-started,
                    effective_exit_code=effective_code)
    (directory/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    if operation=='checkMesh':
        gate=dict(id='gate-'+call_id,handles=[call_id],decision='continue' if outcome=='pass' else 'stop',
                  basis='actual checkMesh process exit and Mesh OK. marker',
                  outcome=outcome,run_id=context['run_id'],input_version=metadata['input_version'])
        (directory/'decision.json').write_text(json.dumps(gate,indent=2)+'\n')
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    sys.exit(effective_code)


if __name__=='__main__':main()
