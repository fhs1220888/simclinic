"""Create or inspect an execution snapshot using read-only local environment probes."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.evaluation.recovery_execution_freeze import build_freeze, verify_freeze


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'verify'))
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--python', dest='python_executable')
    parser.add_argument('--codex', dest='codex_executable')
    parser.add_argument('--docker', dest='docker_executable')
    parser.add_argument('--current-environment', action='store_true')
    args = parser.parse_args(argv)
    options = dict(python_executable=args.python_executable, codex_executable=args.codex_executable,
                   docker_executable=args.docker_executable)
    try:
        if args.action == 'build':
            options['docker_executable'] = args.docker_executable or 'docker'
            result = build_freeze(args.root, args.freeze, **options)
        else:
            result = verify_freeze(args.freeze, root=args.root if args.current_environment else None,
                                   check_current_environment=args.current_environment, **options)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Execution freeze refused/unknown: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
