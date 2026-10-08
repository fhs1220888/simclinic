"""Offline integrity and parameter checks of a completed frozen study."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.evaluation.heldout import verify_hashes, velocity_error, pressure_error
from simclinic.evaluation.sampling_study import verify_sampling_freeze, development_ready


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen, _ = verify_sampling_freeze(batch)
    digest = hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest()
    require(digest == (batch/'freeze.sha256').read_text().strip(), 'Freeze changed')
    progress = json.loads((batch/'progress.json').read_text())
    require(progress['status'] in ('development_complete', 'complete'), 'Stage incomplete: preserve attempts')
    require(progress['status'] != 'development_complete' or development_ready(progress, frozen), 'Development contract not met')
    require([{k:e[k] for k in ('id','task','repeat','arm')} for e in progress['runs']] == (frozen['schedule'] if progress['status'] == 'complete' else []),
            'Actual order differs from predeclared schedule')
    require([e['id'] for e in progress['development']] == ['D0-baseline','D0-candidate'],
            'Development sequence differs from protocol')
    for group in ('development','runs'):
        for entry in progress[group]:
            require(Path(entry['directory']).resolve() == (batch/group/entry['id']).resolve(),
                    'Run directory differs from batch: '+entry['id'])
    verify_hashes(batch/'common-runtime', frozen['common_runtime'])
    for name in ('simclinic/core/record.py', 'simclinic/domains/openfoam/completion.py', 'simclinic/evaluation/heldout.py'):
        require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == frozen['common_runtime'][name],
                'Offline verification helper differs from frozen analysis code: '+name)
    verify_hashes(batch/'corpus', frozen['corpus'])
    for arm in ('baseline','candidate'):
        verify_hashes(batch/'variants'/arm, frozen['variants'][arm])
    for task_id, expected in frozen['tasks'].items():
        verify_hashes(batch/'tasks'/task_id, expected)
    last_end = None
    totals = dict(artifacts=0, input_snapshots=0, model_calls=0, model_tokens=0)
    rows = []
    for entry in progress['development']+progress['runs']:
        require(entry['status'] in ('finished','collection_or_setup_failure'), 'Unfinished run remains: '+entry['id'])
        start, end = datetime.fromisoformat(entry['started_at']), datetime.fromisoformat(entry['ended_at'])
        require(last_end is None or start >= last_end, 'Overlapping evaluation runs')
        last_end = end
        out = Path(entry['directory'])
        summary = json.loads((out/'summary.json').read_text())
        # A failed task can have a complete registered terminal event and intact
        # archived bytes while reference evidence is missing. Verify integrity
        # without upgrading evidence coverage or treating missing samples as met.
        require(summary.get('completed_graph', bool(summary.get('completion'))),
                'No registered terminal result; cannot claim full archive verification: '+entry['id'])
        record = load_record(out/'record.json', out).data
        environment = json.loads((out/'environment.json').read_text())
        require(environment['freeze_sha256'] == digest, 'Run used another freeze')
        require(environment['completion_policy'] == summary['completion_policy'] == frozen['completion_policies'][entry['arm']], 'Wrong policy arm')
        require(environment['acceptance_reference_in_model_context'] is False, 'Held reference leaked')
        verify_hashes(out/'source-package', frozen['variants'][entry['arm']])
        for artifact in record['artifacts']:
            require(hashlib.sha256((out/artifact['path']).read_bytes()).hexdigest() == artifact['sha256'],
                    'Evidence changed: '+entry['id']+'/'+artifact['path'])
        totals['artifacts'] += len(record['artifacts'])
        attempts = []
        for attempt in record['attempts']:
            native = out/'attempts'/attempt['run_id']/'native'
            manifest = json.loads((native/'input-manifest.json').read_text())
            for name, expected in manifest['files'].items():
                require(hashlib.sha256((native/'input-snapshot'/name).read_bytes()).hexdigest() == expected,
                        'Attempt input changed')
            version = 'sha256:'+hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            require(version == attempt['input_version'] == manifest['input_version'], 'Attempt identity changed')
            attempts.append(native/'input-snapshot')
        totals['input_snapshots'] += len(attempts)
        metadata = [json.loads(p.read_text()) for p in sorted((out/'model-calls').glob('*/metadata.json'))]
        require(len(metadata) == summary['model_usage']['total_calls'], 'Model call count mismatch')
        tokens = sum(c['total_tokens'] for c in metadata)
        require(tokens == summary['model_usage']['total_tokens'] == record['metrics']['model_tokens'], 'Usage sum mismatch')
        totals['model_calls'] += len(metadata)
        totals['model_tokens'] += tokens
        measurements = summary['acceptance']
        spec = json.loads((batch/'tasks'/entry['task']/'spec.json').read_text())
        sampling = out/'observations/sampling'
        if measurements.get('profile_error') is not None:
            require(abs(velocity_error(sampling/'profile.xy',spec)-measurements['profile_error']) < 1e-12,
                    'Velocity reference differs from task parameters')
            require(abs(pressure_error(sampling/'pressure.xy',spec)-measurements['pressure_gradient_error']) < 1e-12,
                    'Pressure reference differs from task parameters')
        reference = batch/'tasks'/entry['task']/'acceptance-reference'
        reference_files = [p for folder in ('0','constant','system') for p in (reference/folder).rglob('*') if p.is_file()]
        input_met = bool(attempts) and all((attempts[-1]/p.relative_to(reference)).is_file() and
            canonical_dictionary(attempts[-1]/p.relative_to(reference)) == canonical_dictionary(p) for p in reference_files)
        require(input_met == measurements.get('task_inputs_verified'), 'Task input assessment differs from archived input')
        graph = json.loads((out/'graph-result.json').read_text())
        fields = graph.get('visualization_summary',{}).get('fields_visualized')
        rows.append(dict(id=entry['id'], coordinator_status=entry['status'],
            quality_met=summary['quality_met'], evidence_complete=summary.get('evidence_complete'),
            acceptance_error=json.loads((out/'observations/task-assessment.json').read_text()).get('error'),
            rendered_fields=fields,
            archived_input_checks_recomputed=True, task_reference_errors_recomputed=measurements.get('profile_error') is not None,
            artifact_hashes_verified=len(record['artifacts']), actual_model_calls=len(metadata), actual_model_tokens=tokens))
    require(totals['model_tokens'] == progress['consumed_model_tokens'], 'Batch total usage mismatch')
    output = dict(offline_verification=True, new_simulation_run=False, new_model_calls=0, stage=progress['status'], optimization_effect_proven=False, freeze_sha256=digest,
        verification_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        verification_helpers_match_frozen_code=True,
        schedule_and_serial_order_verified=True, frozen_files_and_run_source_verified=True,
        development_runs=len(progress['development']), held_runs=len(progress['runs']), totals=totals, runs=rows)
    write_json(batch/('development-verification.json' if progress['status'] == 'development_complete' else 'archive-verification.json'),output)
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
