"""Verify a frozen design without authorizing or starting an evaluation."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.inventory_couette_tasks import inventory, unique_json
from simclinic.domains.openfoam.input_contract import contract_task, compare_case, POLICY
from simclinic.domains.openfoam.reference_tasks import create_reference


def read(path):
    def nonfinite(value):
        raise ValueError('Nonfinite JSON: ' + value)
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_json, parse_constant=nonfinite)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(root, relative):
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or '..' in parsed.parts or parsed.as_posix() != relative:
        raise ValueError('Unsafe frozen path')
    path = Path(root) / relative
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Frozen path escaped root')
    for item in (path,) + tuple(path.parents):
        if item.is_symlink():
            raise ValueError('Symlink frozen source')
        if item == Path(root):
            break
    return path


def fingerprint(spec):
    return Fraction(str(spec['wall_speed'])), spec['nx'], spec['ny']


def unused(tasks, catalog):
    if catalog.get('scan_complete') is not True or catalog.get('errors'):
        raise ValueError('Incomplete archive inventory; task novelty unknown')
    known = {fingerprint(spec) for spec in catalog['combinations']}
    if any(fingerprint(spec) in known for spec in tasks):
        raise ValueError('Held task matches a previously used parameter/input combination')
    # Registered single-file parser fixtures are explicitly outside the full
    # task catalog, and retain unknown parameters in the inventory. Other
    # partial sources cannot be silently treated as complete negative evidence.
    for partial in catalog.get('partial_inputs', []):
        if partial.get('registered_single_file_fixture') is not True:
            raise ValueError('Unclassified partial task source; novelty unknown')


def verify_design(directory, rescan=False):
    directory = Path(directory).resolve()
    frozen = read(directory / 'freeze.json')
    digest = sha(directory / 'freeze.json')
    if (directory / 'freeze.sha256').read_text().strip() != digest:
        raise ValueError('Design freeze changed')
    if frozen.get('status') != 'frozen_design_only' or frozen.get('execution_ready') is not False:
        raise ValueError('Design must not assert execution readiness')
    if any(p.is_symlink() for p in directory.rglob('*')):
        raise ValueError('Symlink frozen design')
    actual = {str(p.relative_to(directory)): sha(p) for p in directory.rglob('*')
              if p.is_file() and p.name not in ('freeze.json', 'freeze.sha256') and '__pycache__' not in p.parts}
    if actual != frozen['files']:
        raise ValueError('Frozen design bytes/file set changed')
    for name, expected in frozen['files'].items():
        if sha(safe(directory, name)) != expected:
            raise ValueError('Design file differs')
    for name, expected in frozen['source_files'].items():
        if sha(safe(ROOT, name)) != expected:
            raise ValueError('Design source/basis changed: ' + name)
    config = read(directory / 'protocol.json')
    if (config['status'] != 'frozen_design_only' or config['execution_ready'] is not False
            or config['execution_authorized'] is not False or config['environment_freeze_pending'] is not True):
        raise ValueError('Design cannot authorize execution or claim a current runtime freeze')
    tasks, development = config['heldout_tasks'], config['development_tasks']
    if len(tasks) != 4 or len(development) != 2 or len({fingerprint(s) for s in tasks}) != 4:
        raise ValueError('Unexpected task matrix')
    if {s['id'] for s in tasks} != {'C7', 'C8', 'C9', 'C10'}:
        raise ValueError('Wrong heldout IDs')
    unused(tasks, read(directory / 'inventory.json'))
    if rescan:
        unused(tasks, inventory(ROOT))
    all_tasks = {s['id']: s for s in development + tasks}
    entries = config['entries']
    if config['schedule'] != [e['id'] for e in entries] or len({e['id'] for e in entries}) != 76:
        raise ValueError('Schedule differs or repeats a run')
    expected = Counter((stage, spec['id'], arm, repeat, scenario)
                       for stage, group, repeats, scenario in (
                           ('development', development, 1, 'unmodified'),
                           ('natural', tasks, 6, 'unmodified'),
                           ('injected', tasks, 3, 'transient_missing'))
                       for spec in group for arm in ('baseline', 'candidate') for repeat in range(1, repeats + 1))
    observed = Counter((e['stage'], e['spec_id'], e['arm'], e['repeat'], e['scenario']) for e in entries)
    if expected != observed or [e['stage'] for e in entries] != ['development'] * 4 + ['natural'] * 48 + ['injected'] * 24:
        raise ValueError('Unbalanced matrix or stage order')
    if config['counts'] != dict(development=4, natural=48, injected=24, total=76,
                               natural_repeats_per_task_arm=6, injected_repeats_per_task_arm=3):
        raise ValueError('Reported counts differ from the frozen schedule')
    recovery = config['recovery']
    if (recovery['trigger'] != 'sampling_missing_output' or recovery['maximum_extra_sampler_calls'] != 1
            or recovery['first_exit_code'] != 0 or recovery['solver_retry_by_recovery'] is not False
            or recovery['physics_numerics_acceptance_changes'] is not False):
        raise ValueError('Recovery boundary changed')
    if config['natural']['injection_enabled'] is not False or config['metrics']['natural_injected_never_pooled'] is not True:
        raise ValueError('Natural and injected data must stay separate')
    if (config['injected'].get('require_original_full_gate_pass_before_mutation') is not True
            or config['natural'].get('execution_started_field_is_not_launch_proof') is not True
            or config['reporting'].get('fully_recorded_sampler_timeout_or_unknown_exit_continues_as_task_outcome') is not True
            or config['metrics']['unknown_bounds'].get('confidence_interval') is not False):
        raise ValueError('Observation, unknown-value or complete pre-injection gate boundary changed')
    budgets = config['budgets']
    if (sum(budgets['stage_token_boundaries'].values()) != budgets['usage_token_total']
            or budgets['usage_token_total'] != 22_000_000 or budgets['reserve_before_run'] != 450_000
            or budgets['extend_to_obtain_failures'] is not False or budgets['stop_unknown_usage'] is not True):
        raise ValueError('Budget or finite observation window changed')
    if sum(budgets['stage_wall_seconds'].values()) != budgets['wall_seconds_total']:
        raise ValueError('Wall-time budget mismatch')
    with tempfile.TemporaryDirectory(prefix='simclinic-protocol-contract-') as temporary:
        for task_id, spec in all_tasks.items():
            folder = directory / 'definitions' / task_id
            if read(folder / 'spec.json') != spec or read(folder / 'task.json') != contract_task(spec):
                raise ValueError('Task/quality contract differs: ' + task_id)
            expected_case = create_reference(ROOT / 'integration/foam_agent_v210/reference-corpus-v1/reference-case',
                                             Path(temporary) / task_id, spec)
            checks, _ = compare_case(folder / 'reference', expected_case, spec)
            if len(checks) != 9 or not all(value is True for value in checks.values()):
                raise ValueError('Reference input contract differs: ' + task_id)
            if (folder / 'reference/Allrun').read_bytes() != (expected_case / 'Allrun').read_bytes():
                raise ValueError('Reference command contract differs: ' + task_id)
    return dict(protocol_id=config['protocol_id'], freeze_sha256=digest, design_integrity_verified=True,
                task_catalog_novelty_verified=True, archive_rescanned=rescan, novelty_scope='local archived full-task/input-pair catalog; partial parser fixtures remain unknown',
                quality_contracts_verified=6, input_policy=POLICY, counts=config['counts'],
                usage_token_planning_boundary=budgets['usage_token_total'], execution_ready=False,
                execution_authorized=False, new_model_calls=0, new_simulations=0, new_sampling_calls=0,
                pending=config['execution_prerequisites'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, default=ROOT / 'protocols/bounded-recovery-observation-v1')
    parser.add_argument('--rescan-archive', action='store_true', help='Recheck current local task use before an execution freeze')
    args = parser.parse_args(argv)
    try:
        result = verify_design(args.protocol, args.rescan_archive)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Protocol design refused/unknown: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
