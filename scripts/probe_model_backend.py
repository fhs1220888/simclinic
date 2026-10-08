"""One explicit readiness request; separate from all CFD/held results."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.backends.codex_cli import CodexCLI
from simclinic.core.report import write_json
from scripts.run_full_agent import CODEX


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default='gpt-6.1-sol')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    backend = CodexCLI(out / 'model-calls', args.model, executable=CODEX, timeout=90, max_calls=1)
    result = dict(synthetic=False, backend_readiness_only=True, heldout=False, new_simulations=0,
                  old_failed_request_replayed=False, automatic_retry=False, model=args.model,
                  optimization_effect_proven=False)
    try:
        response = backend.invoke('Return exactly the string SIMCLINIC_READY in the text field. Do not use tools.')
        result.update(success=response.strip() == 'SIMCLINIC_READY', response_matches=response.strip() == 'SIMCLINIC_READY')
    except Exception as exc:
        result.update(success=False, failure=dict(type=type(exc).__name__, message=str(exc)))
    result['model_usage'] = backend.get_statistics()
    result['failure_diagnostic'] = backend.calls[-1].get('failure_diagnostic') if backend.calls else None
    write_json(out / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['success'] else 2


if __name__ == '__main__':
    sys.exit(main())
