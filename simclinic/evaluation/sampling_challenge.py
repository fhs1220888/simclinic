"""Explicit zero-model-development or declared held challenge at sampler return."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace

from ..domains.openfoam import sampling_gate
from ..evaluation.failure_development import SAMPLE_COMMAND
from ..foam_runtime import verify_workspace
from ..core.report import write_json


@contextmanager
def sampling_challenge(recorder, scenario):
    if scenario not in ('healthy', 'transient_missing', 'persistent_missing'):
        raise ValueError('Unknown sampling challenge')
    if scenario == 'healthy':
        yield
        return
    original = sampling_gate.subprocess
    count = 0

    def run(argv, **kwargs):
        nonlocal count
        if argv[:2] != ['docker','run'] or argv[-1] != SAMPLE_COMMAND:
            return original.run(argv, **kwargs)
        count += 1
        result = original.run(argv, **kwargs)
        if scenario == 'transient_missing' and count > 1:
            return result
        mount = next(x for x in argv if x.startswith('type=bind,src='))
        work = Path(mount.split('src=',1)[1].split(',dst=',1)[0])
        marker = verify_workspace(work)
        files = sorted((work/'postProcessing').rglob('profile.xy'))
        if result.returncode != 0 or len(files) != 1:
            raise ValueError('Challenge requires one real fresh profile and zero process exit')
        target = files[0]
        if target.is_symlink() or work.resolve() not in target.resolve().parents:
            raise ValueError('Unsafe challenge output')
        payload = target.read_bytes()
        folder = recorder.directory/'observations/challenge'/('sample-'+str(count))
        folder.mkdir(parents=True, exist_ok=False)
        archive = folder/'removed-profile.xy'
        archive.write_bytes(payload)
        context = {k:marker[k] for k in ('run_id','input_version')}
        data = dict(scenario=scenario, sample_number=count, actor='Explicit development/evaluation injector',
            natural_failure=False, actual_argv=argv, actual_exit_code=result.returncode,
            removed_path=str(target.relative_to(work)), removed_sha256=hashlib.sha256(payload).hexdigest(),
            payload_artifact=recorder.artifact(archive,context), original_reader_manifest=marker['input_files'],
            physical_parameters_changed=False, acceptance_changed=False, **context)
        target.unlink()
        verify_workspace(work)
        data['original_reader_bytes_unchanged']=True
        path=folder/'intervention.json'
        write_json(path,data)
        recorder.event('observation',context=context,observation_type='explicit_sampling_challenge',source_artifact=recorder.artifact(path,context))
        return result

    sampling_gate.subprocess = SimpleNamespace(run=run, TimeoutExpired=original.TimeoutExpired)
    try:
        yield
    finally:
        sampling_gate.subprocess = original
