"""Two-stage frozen coordinator; preserve all observations and usage."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.report import write_json
from simclinic.evaluation.sampling_study import verify_sampling_freeze, development_ready
from simclinic.evaluation.idle_guard import start_idle_guard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--stage', choices=('development', 'heldout'), required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    if ROOT != batch / 'common-runtime':
        raise ValueError('Execute the frozen coordinator')
    frozen, digest = verify_sampling_freeze(batch)
    guard = start_idle_guard(batch, frozen, args.stage)
    path = batch / 'progress.json'
    if args.stage == 'development':
        if path.exists():
            raise ValueError('Development already started; preserve existing attempts')
        progress = dict(started_at=datetime.now(timezone.utc).isoformat(), status='development_running',
                        freeze_sha256=digest, planned_runs=frozen['planned_runs'], development=[], runs=[], consumed_model_tokens=0)
    else:
        progress = json.loads(path.read_text())
        if progress['freeze_sha256'] != digest or progress['status'] != 'development_complete' or not development_ready(progress, frozen):
            raise ValueError('Held launch requires both frozen D0 runs to pass with known usage; no selective restart')
        # Independently recheck run evidence before crossing the stage boundary.
        subprocess.run([sys.executable, str(ROOT / 'scripts/verify_sampling_study.py'), '--batch', str(batch)], check=True)
        progress['status'] = 'running'
    write_json(path, progress)

    def run(entry, development=False):
        group = 'development' if development else 'runs'
        out = batch / group / entry['id']
        row = dict(entry, started_at=datetime.now(timezone.utc).isoformat(), status='running', directory=str(out))
        progress[group].append(row)
        write_json(path, progress)
        log = batch / 'execution-logs' / (entry['id'] + '.log')
        log.parent.mkdir(exist_ok=True)
        print('START', entry['id'], flush=True)
        with log.open('x') as stream:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_sampling_study_run.py'),
                '--batch', str(batch), '--arm', entry['arm'], '--task', entry['task'], '--out', str(out)],
                stdout=stream, stderr=subprocess.STDOUT)
        row.update(exit_code=result.returncode, ended_at=datetime.now(timezone.utc).isoformat(),
                   status='finished' if result.returncode == 0 else 'collection_or_setup_failure')
        summary = json.loads((out / 'summary.json').read_text()) if (out / 'summary.json').exists() else {}
        row.update(quality_met=summary.get('quality_met', False), evidence_complete=summary.get('evidence_complete', False),
                   model_tokens=summary.get('model_usage', {}).get('total_tokens'))
        if row['model_tokens'] is not None:
            progress['consumed_model_tokens'] += row['model_tokens']
        write_json(path, progress)
        subprocess.run([sys.executable, str(ROOT / 'scripts/report_sampling_study.py'), '--batch', str(batch)], check=True)
        print('END', entry['id'], json.dumps(row), flush=True)
        return row

    entries = ([dict(id='D0-' + arm, task='D0', arm=arm, repeat=1) for arm in ('baseline', 'candidate')]
               if args.stage == 'development' else frozen['schedule'])
    for entry in entries:
        observed = progress['development'] + progress['runs']
        if any(r.get('model_tokens') is None for r in observed):
            progress['status'] = 'usage_unknown'
            break
        limit = frozen['development_model_token_limit'] if args.stage == 'development' else frozen['batch_model_token_limit']
        if progress['consumed_model_tokens'] + frozen['reserve_per_run_model_tokens'] > limit:
            progress['status'] = 'budget_boundary'
            break
        row = run(entry, development=args.stage == 'development')
        if args.stage == 'development' and not (row['status'] == 'finished' and row['quality_met'] and row['evidence_complete']):
            progress['status'] = 'development_contract_failed'
            break
    else:
        progress['status'] = 'development_complete' if args.stage == 'development' and development_ready(progress, frozen) else \
            'development_contract_failed' if args.stage == 'development' else 'complete'
    progress['stage_ended_at'] = datetime.now(timezone.utc).isoformat()
    write_json(path, progress)
    subprocess.run([sys.executable, str(ROOT / 'scripts/report_sampling_study.py'), '--batch', str(batch)], check=True)
    print('STAGE', args.stage, progress['status'], 'held', len(progress['runs']), 'usage', progress['consumed_model_tokens'], flush=True)
    return 0 if progress['status'] in ('development_complete', 'complete') else 2


if __name__ == '__main__':
    sys.exit(main())
