"""In-container observation of supported binaries; no third-party imports."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

case=Path('/case')
operation=sys.argv[1]
if operation not in {'blockMesh','checkMesh','simpleFoam','foamDictionary'}:
    raise ValueError('Unsupported command observation')
context=json.loads((case/'.simclinic/native/current.json').read_text())
folder=case/'.simclinic/native'/context['run_id']/'command-calls'/uuid.uuid4().hex
folder.mkdir(parents=True)
manifest={}
for name in ('0','constant','system'):
    for path in sorted((case/name).rglob('*')):
        relative=path.relative_to(case)
        if path.is_file() and not path.is_symlink() and relative.parts[:2]!=('constant','polyMesh'):
            manifest[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
manifest['Allrun']=hashlib.sha256((case/'Allrun').read_bytes()).hexdigest()
fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
started=time.monotonic()
# PATH resolves the underlying executable; shell functions cannot intercept this call.
result=subprocess.run([shutil.which(operation)]+sys.argv[2:],capture_output=True)
(folder/'stdout.log').write_bytes(result.stdout)
(folder/'stderr.log').write_bytes(result.stderr)
text=(result.stdout+result.stderr).decode(errors='replace')
phase='preflight' if operation=='checkMesh' else ('postprocess' if '-postProcess' in sys.argv else ('solve' if operation=='simpleFoam' else 'other'))
outcome='fail' if result.returncode else ('pass' if operation!='checkMesh' or 'Mesh OK.' in text else 'unknown')
metadata=dict(operation=operation,argv=sys.argv[2:],phase=phase,exit_code=result.returncode,outcome=outcome,
              run_id=context['run_id'],input_version='sha256:'+fingerprint,input_fingerprint=fingerprint,
              elapsed_seconds=time.monotonic()-started,started=started)
(folder/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
