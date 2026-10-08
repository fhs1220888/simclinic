"""Offline verification of the archived development runs; executes no simulation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_run(directory):
    directory = Path(directory)
    record = load_record(directory / 'record.json', directory).data
    for artifact in record['artifacts']:
        require(sha(directory / artifact['path']) == artifact['sha256'],
                'Evidence changed: ' + artifact['path'])
    earlier_tools = set()
    for event in record['events']:
        if event['kind'] == 'decision':
            require(set(event['handles']) <= earlier_tools, 'Decision precedes its tools')
        if event['kind'] == 'tool':
            earlier_tools.add(event['id'])
    summary = json.loads((directory / 'summary.json').read_text())
    result = dict(directory=str(directory.relative_to(ROOT)),
                  artifact_hashes_verified=len(record['artifacts']), audit=summary['audit_summary'])
    if summary.get('evidence_ablation'):
        require(summary['new_simulation_run'] is False, 'Ablation mislabeled as a new run')
        require(summary['audit_summary']['insufficient_evidence'] == 5 and
                summary['audit_summary']['finding'] == 0, 'Missing evidence incorrectly classified')
        return result
    require(all(record['coverage'].values()), 'Registered workflow observation incomplete')
    environment = json.loads((directory / 'environment.json').read_text())
    for name, digest in environment['runtime_sha256'].items():
        require(sha(directory / 'runtime-source' / name) == digest, 'Archived runtime changed')
        require(sha(ROOT / name) == digest, 'Delivered runtime differs from actual run: ' + name)
    for name, digest in environment['source_manifest']['source_files'].items():
        require(sha(directory / 'source-package' / name) == digest, 'Native source changed')
    snapshots = []
    for attempt in record['attempts']:
        native = directory / 'attempts' / attempt['run_id'] / 'native'
        manifest = json.loads((native / 'input-manifest.json').read_text())
        require(manifest['input_version'] == attempt['input_version'], 'Attempt identity mismatch')
        for name, digest in manifest['files'].items():
            require(sha(native / 'input-snapshot' / name) == digest, 'Attempt inputs changed')
        fingerprint = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()
        require('sha256:' + fingerprint == attempt['input_version'], 'Input manifest hash mismatch')
        snapshots.append(manifest['files'])
    claims = [e for e in record['events'] if e['kind'] == 'claim']
    require(len(claims) == 1 and claims[0]['success'] == summary['completion']['success'],
            'Completion is not the actual registered node result')
    claim_index = record['events'].index(claims[0])
    measured = {e['criterion_id'] for e in record['events'][:claim_index] if e['kind'] == 'measurement'}
    require(measured == {c['id'] for c in record['task']['acceptance']}, 'Completion precedes assessment')
    usage = summary['model_usage']
    calls = record['model_calls']
    require(len(calls) == usage['total_calls'] and
            sum(c['total_tokens'] for c in calls) == usage['total_tokens'], 'Usage aggregation mismatch')
    fault = summary['fault']
    if fault == 'recoverable':
        require(len(snapshots) == 2 and summary['completion']['success'], 'Recovery failed')
        changes = [name for name in snapshots[0] if snapshots[0][name] != snapshots[1][name]]
        require(changes == ['system/fvSolution'], 'Recovery changed unrelated input files')
        require(summary['audit_summary']['pass'] == 5, 'Recovery lineage audit failed')
        result['changed_input_files'] = changes
    elif fault == 'persistent':
        require(len(snapshots) == 2 and snapshots[0] == snapshots[1], 'Persistent failure inputs differ')
        require(not summary['completion']['success'] and summary['audit_summary']['finding'] == 1,
                'Persistent failure is not honestly reported and detected')
        require(not any(e['kind'] == 'reference' for e in record['events']), 'Failed run consumed outputs')
    else:
        require(summary['completion']['success'] and summary['audit_summary']['pass'] == 4 and
                summary['audit_summary']['not_applicable'] == 1, 'Normal workflow audit failed')
    result.update(attempts=len(snapshots), actual_model_calls=usage['total_calls'],
                  actual_model_tokens=usage['total_tokens'], completion_success=claims[0]['success'],
                  runtime_and_source_hashes_verified=True)
    return result


def verify_patches():
    base = ROOT / 'integration/foam_agent_v210/native/upstream'
    candidate = base.parent / 'observed'
    patches = sorted((ROOT / 'patches/foam-agent-v2.1.0').glob('*.patch'))
    with tempfile.TemporaryDirectory(prefix='simclinic-five-patches-') as temporary:
        shutil.copytree(base, temporary, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        for patch in patches:
            subprocess.run(['git', 'apply', str(patch)], cwd=temporary, check=True, capture_output=True)
        for name, digest in json.loads((candidate / 'candidate-manifest.json').read_text())['source_files'].items():
            require(sha(Path(temporary) / name) == digest, 'Applied candidate differs: ' + name)
        for patch in reversed(patches):
            subprocess.run(['git', 'apply', '--reverse', str(patch)], cwd=temporary, check=True, capture_output=True)
        baseline = json.loads((base / 'source-manifest.json').read_text())['sha256']
        for name, digest in baseline.items():
            require(sha(Path(temporary) / name) == digest, 'Reversed source differs: ' + name)
    return dict(count=len(patches), apply_and_reverse_verified=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'demo-output/observed-verification.json')
    args = parser.parse_args()
    runs = [verify_run(ROOT / 'real-runs' / name) for name in (
        'observed-full-final-20261004', 'observed-recovery-20261004',
        'observed-exhausted-20261004', 'observed-missing-evidence-20261004')]
    result = dict(offline_archive_verification=True, new_simulation_run=False,
                  runs=runs, patches=verify_patches(), optimization_effect_proven=False)
    write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
