"""Temporary macOS idle-sleep assertion scoped to one coordinator process."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import time

from ..core.report import write_json


def start_idle_guard(batch, frozen, stage):
    spec = frozen['idle_sleep_guard']
    command = Path(spec['command'])
    if str(command) != '/usr/bin/caffeinate' or hashlib.sha256(command.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError('Idle guard command differs from freeze')
    process = subprocess.Popen([str(command), '-i', '-w', str(os.getpid())],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError('Idle sleep guard failed to start; no model run launched')
    evidence = dict(started_at=datetime.now(timezone.utc).isoformat(), stage=stage,
                    coordinator_pid=os.getpid(), guard_pid=process.pid, command=str(command),
                    command_sha256=spec['sha256'], policy='prevent user idle system sleep while coordinator PID exists',
                    release='Guard exits automatically when coordinator exits', persistent_settings_changed=False)
    write_json(Path(batch) / ('idle-guard-' + stage + '.json'), evidence)
    return process
