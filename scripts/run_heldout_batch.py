"""Counterbalanced serial evaluation; persist failures and never tune held tasks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import verify_hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen = json.loads((batch/'freeze.json').read_text())
    if ROOT != batch/'common-runtime':
        raise ValueError('Use the frozen coordinator')
    verify_hashes(ROOT, frozen['common_runtime'])
    if (batch/'progress.json').exists():
        raise ValueError('Existing batch progress is preserved; do not repeat selective runs')
    progress = dict(started_at=datetime.now(timezone.utc).isoformat(), status='running',
        freeze_sha256=hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest(),
        planned_runs=frozen['planned_runs'], development=[], runs=[], consumed_model_tokens=0)
    write_json(batch/'progress.json', progress)
    def run(entry, development=False):
        out = batch/('development' if development else 'runs')/entry['id']
        entry = dict(entry, started_at=datetime.now(timezone.utc).isoformat(), status='running', directory=str(out))
        target = progress['development'] if development else progress['runs']
        target.append(entry)
        write_json(batch/'progress.json', progress)
        log = batch/'execution-logs'/(entry['id']+'.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        print('START', entry['id'], flush=True)
        with log.open('w') as stream:
            result = subprocess.run([sys.executable, str(ROOT/'scripts/run_heldout_run.py'),
                '--batch', str(batch), '--arm', entry['arm'], '--task', entry['task'], '--out', str(out)],
                stdout=stream, stderr=subprocess.STDOUT)
        entry.update(exit_code=result.returncode, ended_at=datetime.now(timezone.utc).isoformat(),
                     status='finished' if result.returncode == 0 else 'collection_or_setup_failure')
        path = out/'summary.json'
        if path.is_file():
            summary = json.loads(path.read_text())
            entry.update(quality_met=summary['quality_met'], evidence_complete=summary['evidence_complete'],
                         model_tokens=summary['model_usage']['total_tokens'])
        else:
            entry.update(quality_met=False, evidence_complete=False, model_tokens=None)
        if entry['model_tokens'] is not None:
            progress['consumed_model_tokens'] += entry['model_tokens']
        write_json(batch/'progress.json', progress)
        print('END', entry['id'], json.dumps({k:entry[k] for k in ('status','quality_met','evidence_complete','model_tokens')}), flush=True)
        subprocess.run([sys.executable, str(ROOT/'scripts/report_heldout.py'), '--batch', str(batch)], check=True)
        return entry
    # Shared graph protocol verified using only the already-developed 0.01 task.
    for arm in ('baseline', 'candidate'):
        entry = run(dict(id='D0-'+arm, task='D0', arm=arm, repeat=1), development=True)
        path = Path(entry['directory'])/'summary.json'
        summary = json.loads(path.read_text()) if path.is_file() else {}
        cfd = all(summary.get('acceptance',{}).get(name) is True for name in ('execution_end','steady_converged','task_inputs_verified'))
        expected = summary.get('quality_met') == frozen.get('development_quality_expected',
            {'baseline':False,'candidate':True})[arm]
        if not (entry['evidence_complete'] and cfd and expected):
            progress['status'] = 'development_contract_failed'
            write_json(batch/'progress.json', progress)
            print('Development contract failed; held tasks were not run. Preserve this freeze.', flush=True)
            return 2
    for entry in frozen['schedule']:
        if any(row['model_tokens'] is None for row in progress['development']+progress['runs']):
            progress['status'] = 'usage_unknown'
            break
        if progress['consumed_model_tokens'] + 450000 > frozen['batch_model_token_limit']:
            progress['status'] = 'budget_boundary'
            break
        run(entry)
        # Collector failures remain in the denominator. Do not change source or rerun a task.
    else:
        progress['status'] = 'complete'
    progress['ended_at'] = datetime.now(timezone.utc).isoformat()
    write_json(batch/'progress.json', progress)
    subprocess.run([sys.executable, str(ROOT/'scripts/report_heldout.py'), '--batch', str(batch)], check=True)
    print('BATCH', progress['status'], 'held runs', len(progress['runs']), 'usage', progress['consumed_model_tokens'], flush=True)
    return 0 if progress['status'] == 'complete' else 2


if __name__ == '__main__':
    sys.exit(main())
