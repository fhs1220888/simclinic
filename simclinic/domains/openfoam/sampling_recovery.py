"""Candidate: at most one fresh, verified retry for a known missing output.

No solver, physics, acceptance, model or input repair. The first sampler's
bytes/events remain immutable. Retry bytes occupy a separate registered root.
"""
from pathlib import Path

from .sampling_gate import SamplingError
from .reference_tasks import ReferenceTaskCompletion
from ...foam_runtime import verify_workspace, verified_workspace, current_record


class _ArchiveView:
    def __init__(self, recorder, directory):
        object.__setattr__(self, '_recorder', recorder)
        object.__setattr__(self, 'directory', Path(directory))

    def __getattr__(self, name):
        return getattr(self._recorder, name)

    def __setattr__(self, name, value):
        setattr(self._recorder, name, value)

    def tool(self, *args, **kwargs):
        args = list(args)
        if args[0] == 'sample':
            # Archive bookkeeping is not an effective input/argument change.
            # Persistent repeated failures must remain visible to retry checks.
            kwargs['output_archive_root'] = str((self.directory/'observations/sampling').relative_to(self._recorder.directory))
        return self._recorder.tool(*args, **kwargs)


class BoundedSamplingRecovery:
    """Mixin for the exact registered gate; one missing-output retry only."""
    def sample(self, work, context):
        try:
            return super().sample(work, context)
        except SamplingError as exc:
            if exc.code != 'sampling_missing_output' or getattr(self, 'recovery_attempts', 0):
                raise
            base = self.recorder
            failed = next(e for e in reversed(base.data['events']) if e.get('operation') == 'sample')
            if failed['exit_code'] != 0 or failed['outcome'] != 'fail' or failed['source_artifact'] != exc.assessment_artifact:
                raise
            marker = verify_workspace(work)
            current_record(marker['source_case'], context)
            self.recovery_attempts = 1
            base.event('decision', context=context, handles=[failed['id']],
                decision_type='bounded_postprocess_recovery', action='retry',
                source_artifact=exc.assessment_artifact, retry_limit=1,
                reason='Known missing output; fresh verified workspace; no solver or input changes')
            directory = base.directory/'observations/sampling-recovery/attempt-2'
            if directory.exists():
                raise ValueError('Retry archive must be fresh')
            self.recorder = _ArchiveView(base, directory)
            try:
                with verified_workspace(marker['source_case'], context) as retry_work:
                    result = super().sample(retry_work, context)
                current_record(marker['source_case'], context)
                return result
            finally:
                self.recorder = base


class RecoveringReferenceCompletion(BoundedSamplingRecovery, ReferenceTaskCompletion):
    pass
