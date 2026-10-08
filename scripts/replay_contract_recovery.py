"""Read-only, stdlib archive regression; no model, Docker or solver execution."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.evaluation.recovery_replay import LATEST_FREEZE_SHA256, LATEST_RESULTS_SHA256, replay


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, default=ROOT / 'real-runs/full-model-contract-recovery-study-20261006')
    parser.add_argument('--out', type=Path, required=True, help='New output directory outside the source batch')
    parser.add_argument('--run', dest='run_ids', action='append', help='Export selected run IDs; full batch is always verified')
    parser.add_argument('--freeze-sha256', default=LATEST_FREEZE_SHA256, help='Explicit trusted digest; defaults to the latest frozen study')
    parser.add_argument('--results-sha256', default=LATEST_RESULTS_SHA256, help='Explicit trusted digest; defaults to the latest primary results')
    args = parser.parse_args(argv)
    try:
        result = replay(args.batch, args.out, args.run_ids, args.freeze_sha256, args.results_sha256)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('replay-contract-recovery: rejected/unknown archive: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ('archive_verified', 'verified_runs', 'exported_runs',
                                                  'sampling_recovered', 'totals', 'new_model_calls', 'new_simulations')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
