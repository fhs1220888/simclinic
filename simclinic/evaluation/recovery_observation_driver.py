"""Serial, append-only execution accounting for the frozen observation pilot.

This module is standard-library only. Offline checks never launch a backend.
Recorded sampling uncertainty is a task outcome; missing collection is separate.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import uuid


class BudgetBoundary(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def writable_new(directory):
    directory = Path(directory).absolute()
    for ancestor in (directory,) + tuple(directory.parents):
        if ancestor.is_symlink():
            raise ValueError('Symlink output is forbidden')
        if (ancestor / 'freeze.json').exists() or (ancestor / 'freeze.sha256').exists():
            raise ValueError('Never write inside a frozen archive')
    if directory.exists():
        raise ValueError('Fresh output required; no resume or selective reruns')
    return directory


def valid_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


class Ledger:
    """STARTED is durable before dispatch, even if no final record is produced."""
    def __init__(self, path, protocol, freeze_sha256, *, create=False):
        self.path = Path(path)
        self.protocol = protocol
        self.freeze_sha256 = freeze_sha256
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=False)
        self.events = self.load()

    def load(self):
        events = []
        previous = None
        if not self.path.is_file():
            raise ValueError('Missing STARTED ledger')
        for line in self.path.read_text().splitlines():
            # Reuse the duplicate-key/nonfinite parser without a temporary file.
            def unique(items):
                obj = {}
                for key, value in items:
                    if key in obj:
                        raise ValueError('Duplicate ledger key')
                    obj[key] = value
                return obj
            value = json.loads(line, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite ledger')))
            signature = value.pop('sha256', None)
            if (value.get('sequence') != len(events) + 1 or value.get('previous_sha256') != previous
                    or value.get('freeze_sha256') != self.freeze_sha256 or digest(value) != signature):
                raise ValueError('Ledger chain/freeze mismatch')
            value['sha256'] = signature
            events.append(value)
            previous = signature
        self.validate(events)
        return events

    def validate(self, events):
        active = None
        started = []
        executions = set()
        last_time = None
        for event in events:
            stamp = datetime.fromisoformat(event['at'])
            if stamp.tzinfo is None or (last_time is not None and stamp < last_time):
                raise ValueError('Nonmonotonic ledger timestamp')
            last_time = stamp
            if event['kind'] == 'STARTED':
                if active is not None or len(started) >= len(self.protocol['entries']):
                    raise ValueError('Overlapping or excess task starts')
                declared = self.protocol['entries'][len(started)]
                if event['entry'] != declared or event['execution_id'] in executions:
                    raise ValueError('Not a fresh fixed schedule prefix')
                executions.add(event['execution_id'])
                started.append(event)
                active = event
            elif event['kind'] == 'FINAL':
                if (active is None or event['execution_id'] != active['execution_id']
                        or event['id'] != active['entry']['id'] or not isinstance(event.get('row_sha256'), str)):
                    raise ValueError('Final record without matching active STARTED')
                active = None
            else:
                raise ValueError('Unknown ledger event')
        return started

    def append(self, kind, **fields):
        event = dict(sequence=len(self.events) + 1, previous_sha256=self.events[-1]['sha256'] if self.events else None,
                     freeze_sha256=self.freeze_sha256, kind=kind, at=now(), **fields)
        event['sha256'] = digest(event)
        self.validate(self.events + [event])
        # One line + flush/fsync before returning STARTED to the coordinator.
        import os
        with self.path.open('a') as stream:
            stream.write(canonical(event).decode() + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(event)
        return event

    def start(self, entry, *, elapsed_total_seconds=0, elapsed_stage_seconds=0):
        return self.append('STARTED', entry=entry, execution_id=uuid.uuid4().hex,
                           elapsed_total_seconds=elapsed_total_seconds, elapsed_stage_seconds=elapsed_stage_seconds)

    def finish(self, row):
        return self.append('FINAL', id=row['id'], execution_id=row['execution_id'], row_sha256=digest(row))

    def starts(self):
        return [dict(id=e['entry']['id'], execution_id=e['execution_id'], started_at=e['at'])
                for e in self.events if e['kind'] == 'STARTED']


def known_tokens(rows):
    total = 0
    complete = True
    for row in rows:
        value = row.get('consumption', {}).get('model_tokens')
        lower = row.get('consumption_known', {}).get('model_tokens', 0)
        if value is None:
            complete = False
            if valid_number(lower):
                total += lower
        elif valid_number(value):
            total += value
        else:
            raise ValueError('Invalid model usage')
    return total, complete


def budget_reason(protocol, rows, stage, elapsed_total, elapsed_stage, *, before_run=True):
    """Soft boundaries: reserve before task, known usage after call/task."""
    limits = protocol['budgets']
    total, complete = known_tokens(rows)
    stage_tokens, stage_complete = known_tokens([r for r in rows if r.get('stage') == stage])
    if not complete or not stage_complete:
        return 'usage_unknown'
    if not valid_number(elapsed_total) or not valid_number(elapsed_stage):
        return 'wall_clock_unknown'
    reserve = limits['reserve_before_run'] if before_run else 0
    if (total + reserve > limits['usage_token_total']
            or stage_tokens + reserve > limits['stage_token_boundaries'][stage]
            or elapsed_total >= limits['wall_seconds_total']
            or elapsed_stage >= limits['stage_wall_seconds'][stage]):
        return 'budget_boundary'
    return None


def stop_reason(row):
    """Never use tools_complete/unknown sampler outcome as collection failure."""
    if row.get('evidence_verified') is not True:
        return 'collection_failure'
    if row.get('identity_corrupt') or row.get('identity_unverified') or row.get('policy_violation'):
        return 'identity_or_policy_violation'
    if row.get('false_completion'):
        return 'false_completion'
    if known_tokens([row])[1] is False:
        return 'usage_unknown'
    if row.get('budget_interrupted'):
        return 'budget_boundary'
    if row.get('backend_failure'):
        return 'backend_failure'
    if row.get('infrastructure_failure'):
        return 'infrastructure_failure'
    if row.get('stage') == 'injected' and row.get('injection', {}).get('status') != 'applied':
        return 'noncomparable_intervention'
    if row.get('stage') == 'development' and row.get('quality_met') is not True:
        return 'development_quality_failure'
    return None


def verify_registered_evidence(directory, record):
    directory = Path(directory).resolve()
    seen = set()
    for artifact in record.get('artifacts', []):
        rel = Path(artifact['path'])
        path = directory / rel
        if (rel.is_absolute() or '..' in rel.parts or path.is_symlink()
                or directory not in path.resolve().parents or not path.is_file()
                or artifact['id'] in seen):
            raise ValueError('Unsafe/missing/duplicate evidence')
        if any(p.is_symlink() for p in (path,) + tuple(path.parents) if p != directory.parent):
            raise ValueError('Symlink evidence')
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('Registered evidence bytes changed')
        seen.add(artifact['id'])
    for event in record.get('events', []):
        for field in ('source_artifact', 'stdout_artifact', 'stderr_artifact', 'artifact_id'):
            if event.get(field) is not None and event[field] not in seen:
                raise ValueError('Event refers to absent registered evidence')
    return len(seen)


def derive_quality(directory, record):
    from simclinic.checks.behavior import matches
    directory = Path(directory)
    claim_events = [e for e in record.get('events', []) if e.get('kind') == 'claim'
                    and e.get('claim_type') == 'completion']
    declared = record['task']['acceptance']
    assessments = [a for a in record.get('artifacts', [])
                   if a['path'] == 'observations/task-assessment.json']
    independent = False
    assessment = None
    if len(assessments) == 1:
        assessment = read(directory / assessments[0]['path'])
        independent = (assessment.get('criteria') == declared and assessment.get('error') is None
                       and assessment.get('run_id') == record.get('run_id')
                       and assessment.get('input_version') == record.get('input_version')
                       and assessment.get('run_id') is not None
                       and assessment.get('input_version') is not None
                       and all(matches(assessment.get('values', {}).get(c['id']), c) is True for c in declared))
    explicit = bool(claim_events and claim_events[-1].get('success') is True)
    quality = independent and explicit and record.get('collection_status') == 'complete'
    return dict(quality_met=quality, acceptance_verified=independent,
                explicit_completion=explicit, false_completion=explicit and not independent,
                assessment=assessment)


def verify_run_files(directory, row):
    """Every emitted file is sealed, not only the successful registered subset."""
    directory = Path(directory)
    expected = row.get('files')
    if not isinstance(expected, dict):
        raise ValueError('No run file inventory')
    actual = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts
              and p != directory / 'summary.json'}
    if any(p.is_symlink() for p in directory.rglob('*')) or actual != expected:
        raise ValueError('Run bytes/file set differ')
    if (directory / 'record.json').exists():
        record = read(directory / 'record.json')
        verify_registered_evidence(directory, record)
        if record.get('environment', {}).get('execution_id') != row['execution_id']:
            raise ValueError('Record execution identity changed')
        derived = derive_quality(directory, record)
        for key in ('quality_met', 'acceptance_verified', 'explicit_completion', 'false_completion'):
            if row.get('evidence_verified') is True and row.get(key) != derived[key]:
                raise ValueError('Independent quality/completion mismatch: ' + key)
        if row.get('evidence_verified') is True:
            observation = read(directory / 'observation-summary.json')
            for key in ('first_sample', 'recovery', 'injection'):
                if row.get(key) != observation[key]:
                    raise ValueError('Observation differs from sealed summary')
            calls = [read(p) for p in sorted((directory / 'model-calls').glob('*/metadata.json'))]
            tokens = [c.get('total_tokens') for c in calls]
            total = sum(tokens) if all(valid_number(t) for t in tokens) else None
            if (row['consumption']['model_tokens'] != total or row['consumption']['model_calls'] != len(calls)
                    or row.get('backend_failure') != any(c.get('outcome') != 'pass' for c in calls)):
                raise ValueError('Model consumption/outcome differs from raw metadata')
    elif row.get('evidence_verified') is True:
        raise ValueError('Verified row without record')
    return True


def verify_batch(batch, protocol, freeze_sha256, *, bundle=None):
    """Read-only: a missing FINAL is an interrupted STARTED task, never removed."""
    batch = Path(batch)
    ledger = Ledger(batch / 'ledger.jsonl', protocol, freeze_sha256)
    finals = {e['id']: e for e in ledger.events if e['kind'] == 'FINAL'}
    rows = []
    gaps = []
    progress = read(batch / 'progress.json')
    native_identities = set()
    bundle = Path(bundle) if bundle is not None else None
    frozen = read(bundle / 'freeze.json') if bundle is not None else None
    for start in ledger.starts():
        raw_start = next(e for e in ledger.events if e['kind'] == 'STARTED' and e['execution_id'] == start['execution_id'])
        entry = raw_start['entry']
        if budget_reason(protocol, rows, entry['stage'], raw_start.get('elapsed_total_seconds'),
                         raw_start.get('elapsed_stage_seconds')) is not None:
            raise ValueError('Task started after budget/unknown-usage boundary')
        path = batch / 'runs' / start['id'] / 'summary.json'
        if start['id'] not in finals:
            gaps.append(dict(id=start['id'], reason='started_without_final'))
            # A partial file has no durable final seal; consume known lower
            # bounds from saved model metadata, never its asserted success.
            known = 0
            calls = 0
            for meta in sorted((path.parent / 'model-calls').glob('*/metadata.json')):
                data = read(meta)
                if valid_number(data.get('total_tokens')):
                    known += data['total_tokens']
                calls += 1
            entry = next(e for e in protocol['entries'] if e['id'] == start['id'])
            unfinished = progress.get('unfinished_task_wall', {})
            partial_wall = unfinished.get('known_lower_bound_seconds') if unfinished.get('execution_id') == start['execution_id'] else None
            rows.append(dict(**entry, execution_id=start['execution_id'], quality_met=None,
                             evidence_verified=False, consumption=dict(model_tokens=None, model_calls=None),
                             consumption_known=dict(model_tokens=known, model_calls=calls,
                                                    workflow_elapsed_seconds=partial_wall)))
            continue
        if not path.is_file():
            raise ValueError('FINAL has no summary')
        row = read(path)
        if digest(row) != finals[start['id']]['row_sha256'] or row.get('execution_id') != start['execution_id']:
            raise ValueError('Final row bytes/identity differ from ledger')
        if any(row.get(key) != value for key, value in entry.items()):
            raise ValueError('Final row differs from declared entry')
        verify_run_files(path.parent, row)
        if bundle is not None:
            from simclinic.evaluation.heldout import hashes
            if hashes(path.parent / 'source-package') != frozen['native']:
                raise ValueError('Executed native source differs from freeze')
            record = read(path.parent / 'record.json')
            if (record['task'] != read(bundle / 'definitions' / entry['spec_id'] / 'task.json')
                    or record['environment']['freeze_sha256'] != freeze_sha256
                    or record.get('heldout') != (entry['stage'] != 'development')
                    or record.get('fault_injection') != (entry['stage'] == 'injected')):
                raise ValueError('Recorded task/environment/scope differs from freeze')
            for attempt in record.get('attempts', []):
                identity = attempt['run_id']
                if identity in native_identities:
                    raise ValueError('Native result identity reused across attempts/tasks')
                native_identities.add(identity)
                native = path.parent / 'attempts' / identity / 'native'
                manifest = read(native / 'input-manifest.json')
                actual = hashes(native / 'input-snapshot')
                version = 'sha256:' + digest(actual)
                if (actual != manifest['files'] or version != manifest['input_version']
                        or version != attempt['input_version'] or manifest['run_id'] != identity):
                    raise ValueError('Input snapshot/version differs from archived identity')
        rows.append(row)
    progress = read(batch / 'progress.json')
    if progress['freeze_sha256'] != freeze_sha256:
        raise ValueError('Progress freeze mismatch')
    sealed_rows = [r for r in rows if any(e.get('kind') == 'FINAL' and e['id'] == r['id'] for e in ledger.events)]
    if progress.get('rows', sealed_rows) != sealed_rows:
        raise ValueError('Mutable progress differs from sealed FINAL rows')
    if progress.get('status') == 'complete' and (gaps or len(rows) != len(protocol['entries'])):
        raise ValueError('Complete claim without entire sealed schedule')
    for row in rows[:-1]:
        if stop_reason(row) is not None:
            raise ValueError('Started task after mandatory stopping condition')
    return dict(ledger_integrity_verified=True, run_bytes_verified=not gaps,
                started=ledger.starts(), rows=rows, gaps=gaps,
                status=progress['status'], scheduled=len(protocol['entries']), all_started=len(rows))


def offline_contract_checks(protocol):
    """Exercise order, soft budget, honest unknown outcomes and durable failure."""
    entries = protocol['entries']
    if ([e['stage'] for e in entries] != ['development'] * 4 + ['natural'] * 48 + ['injected'] * 24
            or protocol['schedule'] != [e['id'] for e in entries] or len({e['id'] for e in entries}) != 76):
        raise ValueError('Frozen order changed')
    safe = dict(stage='natural', quality_met=False, evidence_verified=True,
                consumption=dict(model_tokens=10), first_sample=dict(function_called=True,
                launch_status='started', missing_output_confirmed=None, eligible=None))
    if stop_reason(safe) is not None:
        raise ValueError('Complete unknown sampler wrongly stops observation')
    if stop_reason(dict(safe, evidence_verified=False)) != 'collection_failure':
        raise ValueError('Lost collection must stop')
    if stop_reason(dict(safe, consumption=dict(model_tokens=None))) != 'usage_unknown':
        raise ValueError('Unknown model usage must stop')
    if stop_reason(dict(safe, stage='injected', injection=dict(status='not_applied'))) != 'noncomparable_intervention':
        raise ValueError('Noncomparable injection must stop')
    for stage in ('development', 'natural', 'injected'):
        limit = protocol['budgets']['stage_token_boundaries'][stage]
        row = dict(stage=stage, consumption=dict(model_tokens=limit - protocol['budgets']['reserve_before_run'] + 1))
        if budget_reason(protocol, [row], stage, 0, 0) != 'budget_boundary':
            raise ValueError('Stage reserve/borrowing boundary failed')
    return dict(order_verified=True, budget_verified=True, stop_rules_verified=True,
                record_integrity_checked_by='hash-chained STARTED/FINAL + all run files + registered artifacts',
                new_model_calls=0, new_simulations=0, new_sampling_calls=0)
