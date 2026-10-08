"""Recompute one immutable observation archive with two fresh Python processes.

Standard library only. This entry point calls the read-only archive reviewer;
it never invokes a model, Docker, CFD solver, sampling, or rendering.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


sys.dont_write_bytecode = True
REVIEWER = Path(__file__).with_name('review_recovery_observation.py').resolve()
MAX_DERIVED_ULPS = 4
ALLOWED_FLOAT_PATHS = (
    ('runs', '*', 'acceptance', 'values', 'profile_error'),
    ('runs', '*', 'acceptance', 'values', 'pressure_gradient_error'),
    ('runs', '*', 'acceptance', 'inlet_face_order', 'west_face_y_centres', '*'),
    ('runs', '*', 'acceptance', 'inlet_face_order', 'expected_inlet_x_velocity', '*'),
    ('runs', '*', 'sampling_policy', 'injection_original_measurements', 'profile_error'),
    ('runs', '*', 'sampling_policy', 'injection_original_measurements', 'pressure_gradient_error'),
)
VERSION_PROBE = """import json, os, platform, sys
print(json.dumps(dict(implementation=platform.python_implementation(),
    version=platform.python_version(), full_version=sys.version,
    version_info=list(sys.version_info), executable=sys.executable,
    prefix=sys.prefix, platform=platform.platform(), process_id=os.getpid(),
    binary64=(sys.float_info.radix,sys.float_info.mant_dig,sys.float_info.max_exp)==(2,53,1024))))
"""


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def fresh_directory(path, batch, freeze):
    path = Path(path).absolute()
    for ancestor in (path,) + tuple(path.parents):
        markers = (ancestor / 'freeze.json', ancestor / 'freeze.sha256')
        if ancestor.is_symlink() or any(marker.exists() or marker.is_symlink() for marker in markers):
            raise ValueError('Output cannot enter a frozen or symlinked tree')
    if path.exists():
        raise ValueError('Output directory must be fresh')
    resolved = path.resolve()
    for tree in (Path(batch).resolve(), Path(freeze).resolve()):
        if resolved == tree or tree in resolved.parents:
            raise ValueError('Output must be outside archive and execution freeze')
    return path


def inventory(tree):
    """Hash every original regular file, rejecting symlinks and special files."""
    tree = Path(tree).resolve()
    if not tree.is_dir():
        raise ValueError('Input tree is not a directory: ' + str(tree))
    result = {}
    for directory, folders, files in os.walk(str(tree), followlinks=False):
        folders.sort()
        files.sort()
        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('Symlink in original tree: ' + str(path))
        for name in files:
            path = Path(directory) / name
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Nonregular original file: ' + str(path))
            result[path.relative_to(tree).as_posix()] = dict(sha256=sha(path), bytes=path.stat().st_size)
    return result


def load_numeric_api(path=REVIEWER):
    spec = importlib.util.spec_from_file_location('_cross_python_archive_review', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.MAX_DERIVED_ULPS != MAX_DERIVED_ULPS:
        raise ValueError('Reviewer numerical rule differs from the audited four-ULP bound')
    return module


def normalized(report):
    """Remove only runtime metadata and retained per-version numerical audits."""
    if type(report) is not dict or type(report.get('python_runtime')) is not dict or type(report.get('runs')) is not list:
        raise ValueError('Reviewer report lacks runtime metadata or run list')
    result = copy.deepcopy(report)
    del result['python_runtime']
    for run in result['runs']:
        for component in ('acceptance', 'sampling_policy'):
            if component not in run:
                continue  # Partial reports retain their exact partial shape.
            item = run[component]
            if type(item) is not dict or type(item.get('numeric_differences')) is not list:
                raise ValueError('Missing per-version numerical audit: ' + component)
            del item['numeric_differences']
    return result


def compare_reports(report_a, report_b, *, numeric_api=None, differences=None):
    numeric_api = numeric_api or load_numeric_api()
    differences = [] if differences is None else differences
    numeric_api.require_derived_match(normalized(report_a), normalized(report_b),
        allowed_float_paths=ALLOWED_FLOAT_PATHS, label='python_a_vs_python_b', differences=differences)
    return dict(status='pass', differing_derived_values=len(differences),
                maximum_observed_ulps=max((item['ulp_distance'] for item in differences), default=0),
                allowed_maximum_ulps=MAX_DERIVED_ULPS, differences=differences,
                all_other_report_fields_exact=True,
                runtime_and_per_version_numeric_audits_retained_in_original_reports=True,
                original_thresholds_changed=False)


def launch(python, arguments, output, stem):
    command = [str(python), '-B'] + [str(value) for value in arguments]
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    completed = subprocess.run(command, cwd=str(output), env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (output / (stem + '.stdout.txt')).write_bytes(completed.stdout)
    (output / (stem + '.stderr.txt')).write_bytes(completed.stderr)
    return dict(command=command, returncode=completed.returncode,
                stdout=stem + '.stdout.txt', stderr=stem + '.stderr.txt')


def verify(python_a, python_b, freeze, batch, out, *, reviewer_path=REVIEWER):
    """Write a new audit directory, retaining failed subprocess diagnostics."""
    freeze, batch = Path(freeze).resolve(), Path(batch).resolve()
    if not freeze.is_dir() or not batch.is_dir():
        raise ValueError('Both batch and freeze must be existing directories')
    output = fresh_directory(out, batch, freeze)
    reviewer_path = Path(reviewer_path).resolve()
    code_paths = dict(entry=Path(__file__).resolve(), reviewer=reviewer_path)
    code_before = {name: dict(path=str(path), sha256=sha(path)) for name, path in code_paths.items()}
    before = dict(batch=inventory(batch), freeze=inventory(freeze))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Exclusive creation; never reuse an earlier failed attempt.
    write(output / 'original-files-before.json', before)
    result = dict(schema_version='1.0', status='fail', batch=str(batch), execution_freeze=str(freeze),
                  output=str(output), code_before=code_before, issues=[], processes={}, reports={},
                  new_model_calls=0, new_simulations=0, new_sampling_calls=0,
                  original_thresholds_changed=False, archived_results_rewritten=False,
                  interpretation_scope='offline reproduction of this archived Couette workflow only')
    differences = []
    try:
        numeric_api = load_numeric_api()
        reports = []
        for arm, python in (('a', python_a), ('b', python_b)):
            version = launch(python, ['-c', VERSION_PROBE], output, 'python-' + arm + '-version')
            result['processes']['python_' + arm + '_version'] = version
            if version['returncode']:
                raise ValueError('Python runtime probe failed: ' + arm)
            runtime = read(output / version['stdout'])
            write(output / ('python-' + arm + '-runtime.json'), runtime)
            if runtime.get('binary64') is not True:
                raise ValueError('Python runtime is not binary64: ' + arm)
            report_path = output / ('review-python-' + arm + '.json')
            process = launch(python, [reviewer_path, '--batch', batch, '--freeze', freeze, '--out', report_path],
                             output, 'review-python-' + arm)
            result['processes']['review_python_' + arm] = process
            if not report_path.is_file():
                raise ValueError('Reviewer produced no report: ' + arm)
            report = read(report_path)
            result['reports'][arm] = dict(path=report_path.name, sha256=sha(report_path), status=report.get('status'),
                                        python_runtime=report.get('python_runtime'), runtime_probe=runtime)
            if report.get('review_script_sha256') != code_before['reviewer']['sha256']:
                raise ValueError('Reviewer code hash does not match invocation: ' + arm)
            observed_runtime = report.get('python_runtime')
            if type(observed_runtime) is not dict or any(runtime.get(key) != value for key, value in observed_runtime.items()):
                raise ValueError('Reviewer runtime metadata differs from actual runtime probe: ' + arm)
            reports.append(report)
            if process['returncode'] or report.get('status') != 'pass':
                raise ValueError('Reviewer did not fully pass: ' + arm)
        result['comparison'] = compare_reports(*reports, numeric_api=numeric_api, differences=differences)
        result['status'] = 'pass'
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result['issues'].append(dict(code='offline_python_verification_failed', detail=str(exc)))
        result['comparison'] = dict(status='fail', differences=differences,
            maximum_observed_ulps=max((item['ulp_distance'] for item in differences), default=0),
            allowed_maximum_ulps=MAX_DERIVED_ULPS)
    finally:
        code_after = {}
        for name, path in code_paths.items():
            try:
                code_after[name] = dict(path=str(path), sha256=sha(path))
            except OSError as exc:
                code_after[name] = dict(path=str(path), sha256=None, error=str(exc))
        result['code_after'] = code_after
        result['code_unchanged'] = code_before == code_after
        try:
            after = dict(batch=inventory(batch), freeze=inventory(freeze))
            write(output / 'original-files-after.json', after)
            result['original_files_unchanged'] = before == after
            result['original_file_counts'] = {name: len(files) for name, files in before.items()}
            result['original_manifests'] = {name: dict(path='original-files-' + name + '.json',
                sha256=sha(output / ('original-files-' + name + '.json'))) for name in ('before', 'after')}
        except (ValueError, OSError) as exc:
            result['original_files_unchanged'] = False
            result['issues'].append(dict(code='original_files_after_unverifiable', detail=str(exc)))
        if not result['code_unchanged'] or not result['original_files_unchanged']:
            result['status'] = 'fail'
            result['issues'].append(dict(code='source_or_original_files_changed_during_review'))
        write(output / 'cross-version-verification.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-a', type=Path, required=True)
    parser.add_argument('--python-b', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    # Resolve executable paths before the subprocess changes its working directory.
    try:
        result = verify(args.python_a.absolute(), args.python_b.absolute(), args.freeze, args.batch, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(dict(status=result['status'], out=result['output'],
                         original_files_unchanged=result['original_files_unchanged'],
                         comparison=result.get('comparison', {}).get('status')), ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
