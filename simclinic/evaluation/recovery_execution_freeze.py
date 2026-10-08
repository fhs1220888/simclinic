"""Archive a current execution configuration without executing an evaluation.

Only local Python metadata, CLI --version and Docker inspection are probed.
No model request, image pull, container launch, solver or sampler is issued.
An archive seal authenticates bytes, not model access, authorization or quality.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys


SCHEMA = 'simclinic-recovery-execution-freeze-v1'
PROTOCOL_RELATIVE = 'protocols/bounded-recovery-observation-v1'
PRIOR_RELATIVE = 'real-runs/full-model-contract-recovery-study-20261006'
NATIVE_RELATIVE = 'integration/foam_agent_v210/native/content'
REQUIREMENTS_RELATIVE = 'integration/foam_agent_v210/native/requirements.lock'
MANIFEST_RELATIVE = 'patches/simclinic-bounded-sampling-recovery/manifest.json'
RUNTIME_SCRIPTS = ('scripts/run_recovery_observation.py',
                   'scripts/check_recovery_observation_protocol.py',
                   'scripts/inventory_couette_tasks.py',
                   'scripts/freeze_recovery_observation_execution.py')
PYTHON_METADATA = '''import importlib.metadata as m,json,platform,sys
rows=sorted({(d.metadata.get("Name") or "unknown",d.version) for d in m.distributions()})
print(json.dumps(dict(version=sys.version,executable=sys.executable,prefix=sys.prefix,
base_prefix=sys.base_prefix,machine=platform.machine(),platform=platform.platform(),
implementation=platform.python_implementation(),dependencies=[n+"=="+v for n,v in rows])))'''


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def _safe(root, relative):
    if not isinstance(relative, str):
        raise ValueError('Frozen path is not text')
    parsed = PurePosixPath(relative)
    if (parsed.is_absolute() or not parsed.parts or '..' in parsed.parts
            or parsed.as_posix() != relative):
        raise ValueError('Unsafe frozen path: ' + relative)
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('Frozen path escaped its root: ' + relative)
    for parent in (path,) + tuple(path.parents):
        if parent.is_symlink():
            raise ValueError('Symlink frozen source: ' + relative)
        if parent == root:
            break
    return path


def _hashes(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Symlink archive tree')
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink archive entry: ' + str(path))
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = sha(path)
    return result


def _probe(argv, timeout=20):
    """Keep local command evidence, including refusal/timeouts, never execute shell."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return dict(argv=list(argv), status='pass' if result.returncode == 0 else 'unknown',
                    exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                    timed_out=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        return dict(argv=list(argv), status='unknown', exit_code=None, stdout=None,
                    stderr=None, timed_out=isinstance(exc, subprocess.TimeoutExpired),
                    error=dict(type=type(exc).__name__, message=str(exc)))


def _binary(executable):
    candidate = shutil.which(str(executable)) if not Path(executable).is_absolute() else str(executable)
    if not candidate:
        return dict(requested=str(executable), path=None, resolved_path=None, sha256=None,
                    status='unknown', reason='executable_not_found')
    path = Path(candidate)
    try:
        return dict(requested=str(executable), path=str(path.absolute()),
                    resolved_path=str(path.resolve()), sha256=sha(path), status='pass')
    except OSError as exc:
        return dict(requested=str(executable), path=str(path.absolute()),
                    resolved_path=str(path.resolve()), sha256=None, status='unknown',
                    reason=str(exc))


def _parsed_probe(evidence):
    if evidence['status'] != 'pass':
        return None
    try:
        return json.loads(evidence['stdout'])
    except (TypeError, ValueError):
        evidence['status'] = 'unknown'
        evidence['parse_error'] = 'command_did_not_return_JSON'
        return None


def observe_environment(root, *, protocol_directory=None, python_executable=None,
                        codex_executable=None, docker_executable='docker',
                        configured_model=None, configured_reasoning_effort=None):
    """Read current local metadata; configured model is not an availability probe."""
    root = Path(root).resolve()
    protocol_directory = Path(protocol_directory or root / PROTOCOL_RELATIVE)
    config = read(protocol_directory / 'protocol.json')
    python_executable = str(python_executable or root / '.venv-native/bin/python')
    if codex_executable is None:
        codex_executable = read(root / PRIOR_RELATIVE / 'freeze.json')['cli']
    codex_executable = str(codex_executable)
    image = config['environment_basis']['image']
    model = configured_model or config['environment_basis']['model']
    effort = configured_reasoning_effort or config['environment_basis']['model_reasoning_effort']
    commands = {}
    commands['python_metadata'] = _probe([python_executable, '-c', PYTHON_METADATA])
    python = _parsed_probe(commands['python_metadata'])
    commands['pip_freeze'] = _probe([python_executable, '-m', 'pip', 'freeze'])
    pip_freeze = (commands['pip_freeze']['stdout'].splitlines()
                  if commands['pip_freeze']['status'] == 'pass' else None)
    commands['codex_version'] = _probe([codex_executable, '--version'])
    commands['docker_version'] = _probe([str(docker_executable), 'version', '--format', '{{json .}}'])
    docker_version = _parsed_probe(commands['docker_version'])
    # Select daemon identity/configuration only, excluding live workload counters.
    info_format = ('{"ID":{{json .ID}},"ServerVersion":{{json .ServerVersion}},'
                   '"OSType":{{json .OSType}},"Architecture":{{json .Architecture}},'
                   '"KernelVersion":{{json .KernelVersion}},"NCPU":{{json .NCPU}},'
                   '"MemTotal":{{json .MemTotal}},"DockerRootDir":{{json .DockerRootDir}}}')
    commands['docker_info'] = _probe([str(docker_executable), 'info', '--format', info_format])
    docker_info = _parsed_probe(commands['docker_info'])
    commands['docker_image_inspect'] = _probe([str(docker_executable), 'image', 'inspect', image])
    image_rows = _parsed_probe(commands['docker_image_inspect'])
    inspected = None
    if isinstance(image_rows, list) and len(image_rows) == 1 and isinstance(image_rows[0], dict):
        inspected = {key: image_rows[0].get(key) for key in
                     ('Id', 'RepoDigests', 'Architecture', 'Os', 'Created', 'RootFS')}
    elif image_rows is not None:
        commands['docker_image_inspect']['status'] = 'unknown'
        commands['docker_image_inspect']['parse_error'] = 'one_image_inspection_required'
    binaries = {name: _binary(executable) for name, executable in
                (('python', python_executable), ('codex', codex_executable),
                 ('docker', docker_executable), ('idle_guard', '/usr/bin/caffeinate'))}
    gaps = [name + '_probe_unknown' for name, value in commands.items() if value['status'] != 'pass']
    gaps.extend(name + '_binary_unknown' for name, value in binaries.items() if value['status'] != 'pass')
    if (inspected is not None and
            (inspected.get('Id') != config['environment_basis']['image_id']
             or inspected.get('Architecture') != 'amd64' or inspected.get('Os') != 'linux')):
        gaps.append('configured_image_identity_or_platform_mismatch')
    if model != config['environment_basis']['model'] or effort != config['environment_basis']['model_reasoning_effort']:
        gaps.append('configured_model_or_effort_differs_from_design')
    lock_path = root / REQUIREMENTS_RELATIVE
    try:
        locked = [line.strip() for line in lock_path.read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith('#')]
        dependency_lock = dict(path=str(lock_path), sha256=sha(lock_path), requirements=locked,
                               matches_installed=(sorted(locked) == sorted(pip_freeze)) if pip_freeze is not None else None)
        if dependency_lock['matches_installed'] is False:
            gaps.append('native_dependency_lock_mismatch')
    except OSError as exc:
        dependency_lock = dict(path=str(lock_path), sha256=None, requirements=None,
                               matches_installed=None, reason=str(exc))
        gaps.append('native_dependency_lock_unknown')
    return dict(schema='simclinic-read-only-environment-observation-v1',
                observed_at=datetime.now(timezone.utc).isoformat(), observation_mode='read_only_local_probes',
                configured=dict(model=model, reasoning_effort=effort, image=image,
                                resources=config['environment_basis']['resources'],
                                agent_commit=config['environment_basis']['agent_commit'],
                                codex_executable=binaries['codex']['path'] or codex_executable,
                                python_executable=binaries['python']['path'] or python_executable,
                                docker_executable=binaries['docker']['path'] or str(docker_executable)),
                idle_sleep_guard=dict(command='/usr/bin/caffeinate', sha256=binaries['idle_guard']['sha256'],
                                      argv_template=['/usr/bin/caffeinate', '-i', '-w', '<coordinator_pid>'],
                                      policy='prevent user idle system sleep while coordinator PID exists',
                                      persistent_settings_changed=False, started=False),
                commands=commands, binaries=binaries, python=python, pip_freeze=pip_freeze,
                dependency_lock=dependency_lock,
                codex_version=commands['codex_version']['stdout'].strip() if commands['codex_version']['status'] == 'pass' else None,
                docker_version=docker_version, docker_info=docker_info, image=image,
                image_inspection=inspected, configured_model=model, configured_reasoning_effort=effort,
                requested_resources=config['environment_basis']['resources'],
                model_availability=dict(status='unknown', probed=False,
                                        reason='No model request was authorized or made; a version probe does not establish model access'),
                environment_gaps=gaps, environment_preflight_pass=not gaps,
                execution_authorized=False, new_model_calls=0, new_simulations=0, new_sampling_calls=0)


def _copy_tree(source, destination):
    expected = _hashes(source)
    destination.mkdir(parents=True, exist_ok=False)
    for name in expected:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_safe(source, name), target)
    if _hashes(destination) != expected:
        raise ValueError('Source changed while copying: ' + str(source))
    return expected


def build_freeze(root, output, *, protocol_directory=None, python_executable=None,
                 codex_executable=None, docker_executable='docker', configured_model=None,
                 configured_reasoning_effort=None, source_paths=None):
    """Create a fresh immutable bundle. It grants no run authorization or C0 pass."""
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Execution freeze destination must be new; preserve existing freezes')
    if any(parent.is_symlink() for parent in output.parents):
        raise ValueError('Symlink execution freeze destination is forbidden')
    for ancestor in (output,) + tuple(output.parents):
        if any((ancestor / marker).exists() or (ancestor / marker).is_symlink()
               for marker in ('freeze.json', 'freeze.sha256')):
            raise ValueError('Execution freeze cannot be nested inside an existing frozen archive')
    for protected in ('real-runs', 'protocols', 'patches', 'integration', 'simclinic', 'scripts'):
        if output.resolve().is_relative_to(root / protected):
            raise ValueError('Execution freeze cannot be written into historical/source trees')
    if root.is_relative_to(output.resolve()):
        raise ValueError('Execution freeze cannot contain its source root')
    protocol_directory = Path(protocol_directory or root / PROTOCOL_RELATIVE).resolve()
    # The existing design verifier is read-only and authenticates its source basis.
    from scripts.check_recovery_observation_protocol import verify_design
    design_review = verify_design(protocol_directory)
    protocol = read(protocol_directory / 'protocol.json')
    prior_path = root / PRIOR_RELATIVE / 'freeze.json'
    prior = read(prior_path)
    if sha(prior_path) != protocol['basis']['prior_freeze_sha256']:
        raise ValueError('Prior source/corpus basis changed')
    if sha(root / MANIFEST_RELATIVE) != protocol['basis']['candidate_manifest_sha256']:
        raise ValueError('Candidate delivery basis changed')
    native_source = root / NATIVE_RELATIVE
    corpus_source = root / PRIOR_RELATIVE / 'corpus'
    if _hashes(native_source) != prior['native'] or _hashes(corpus_source) != prior['corpus']:
        raise ValueError('Native source or C0-only corpus differs from validated basis')
    corpus = read(corpus_source / 'corpus.json')
    if corpus.get('heldout_reference_or_outputs_in_corpus') is not False:
        raise ValueError('Shared corpus must exclude heldout references and outputs')
    native_manifest = read(native_source / 'candidate-manifest.json')
    if native_manifest['base_commit'] != protocol['environment_basis']['agent_commit']:
        raise ValueError('Native Agent commit differs from design')
    runtime_names = (sorted('simclinic/' + name for name in _hashes(root / 'simclinic'))
                     + list(RUNTIME_SCRIPTS) if source_paths is None else sorted(set(source_paths)))
    runtime_sources = {name: sha(_safe(root, name)) for name in runtime_names}
    observation = observe_environment(root, protocol_directory=protocol_directory,
        python_executable=python_executable, codex_executable=codex_executable,
        docker_executable=docker_executable, configured_model=configured_model,
        configured_reasoning_effort=configured_reasoning_effort)
    output.mkdir(parents=True, exist_ok=False)
    trees = {}
    runtime = output / 'common-runtime'
    runtime.mkdir()
    for name in runtime_names:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_safe(root, name), target)
    trees['common-runtime'] = _hashes(runtime)
    if trees['common-runtime'] != runtime_sources:
        raise ValueError('Runtime changed while copying')
    for folder, source in (('native', native_source), ('corpus', corpus_source),
                           ('definitions', protocol_directory / 'definitions'),
                           ('protocol-design', protocol_directory)):
        trees[folder] = _copy_tree(source, output / folder)
    basis = output / 'basis'
    basis.mkdir()
    basis_files = {'prior-freeze.json': prior_path,
                   'candidate-manifest.json': root / MANIFEST_RELATIVE,
                   'requirements.lock': root / REQUIREMENTS_RELATIVE}
    for name, path in basis_files.items():
        shutil.copyfile(path, basis / name)
    trees['basis'] = _hashes(basis)
    _write(output / 'environment.json', observation)
    frozen_files = _hashes(output)
    binary_paths = observation['binaries']
    frozen = dict(schema=SCHEMA, created_at=datetime.now(timezone.utc).isoformat(),
        status='execution_snapshot_only', execution_authorized=False, heldout_ready=False,
        new_c0_development_checks_passed=False, model_availability_verified=False,
        protocol_id=protocol['protocol_id'], design_freeze_sha256=design_review['freeze_sha256'],
        prior_freeze_sha256=sha(prior_path), candidate_manifest_sha256=sha(root / MANIFEST_RELATIVE),
        source_origin=str(root), source_origin_files=runtime_sources, trees=trees,
        frozen_files=frozen_files, environment=observation,
        python_executable=binary_paths['python']['path'] or str(python_executable or root / '.venv-native/bin/python'),
        cli=binary_paths['codex']['path'] or str(codex_executable or prior['cli']),
        docker=binary_paths['docker']['path'] or str(docker_executable),
        image=observation['image'], image_id=observation['image_inspection'].get('Id') if observation['image_inspection'] else None,
        model=observation['configured_model'], configured_model=observation['configured_model'],
        model_reasoning_effort=observation['configured_reasoning_effort'],
        resources=observation['requested_resources'], agent_commit=native_manifest['base_commit'],
        idle_sleep_guard=observation['idle_sleep_guard'],
        budgets=protocol['budgets'], entries=protocol['entries'], schedule=protocol['schedule'],
        counts=protocol['counts'], protocol=protocol,
        common_runtime=trees['common-runtime'], native=trees['native'], corpus=trees['corpus'],
        definitions=trees['definitions'], environment_gaps=observation['environment_gaps'],
        environment_preflight_pass=observation['environment_preflight_pass'],
        pending=['Explicit authorization before any real execution',
                 'Four newly frozen C0 development checks before heldout execution',
                 'Model access remains unprobed until an authorized request'],
        new_model_calls=0, new_simulations=0, new_sampling_calls=0)
    _write(output / 'freeze.json', frozen)
    digest = sha(output / 'freeze.json')
    (output / 'freeze.sha256').write_text(digest + '\n')
    return verify_freeze(output)


def _comparable_environment(observation):
    # Version probes, platform, dependency versions and binaries are stable pins.
    # Probe timestamps and raw error/log formatting are evidence, not identity.
    result = {key: observation.get(key) for key in
            ('binaries', 'python', 'pip_freeze', 'codex_version', 'docker_version',
             'docker_info', 'image', 'image_inspection', 'configured_model',
             'configured_reasoning_effort', 'requested_resources')}
    # A PATH lookup and its resolved absolute command name describe the same
    # executable. Record the user's spelling but do not mistake it for drift.
    result['binaries'] = {name: {key: value.get(key) for key in
                                ('path', 'resolved_path', 'sha256', 'status')}
                          for name, value in observation.get('binaries', {}).items()}
    return result


def verify_freeze(directory, *, root=None, check_current_environment=False,
                  python_executable=None, codex_executable=None, docker_executable=None):
    """Portable bytes verification and optional current-host reinspection are separate."""
    directory = Path(directory).resolve()
    frozen = read(directory / 'freeze.json')
    digest = sha(directory / 'freeze.json')
    if (directory / 'freeze.sha256').read_text().strip() != digest:
        raise ValueError('Execution freeze seal changed')
    if (frozen.get('schema') != SCHEMA or frozen.get('execution_authorized') is not False
            or frozen.get('heldout_ready') is not False or frozen.get('new_c0_development_checks_passed') is not False
            or frozen.get('model_availability_verified') is not False):
        raise ValueError('Execution snapshot cannot grant authorization, model access or C0 quality')
    expected = frozen['frozen_files']
    for name, digest_expected in expected.items():
        path = _safe(directory, name)
        if not path.is_file() or sha(path) != digest_expected:
            raise ValueError('Frozen archive bytes changed: ' + name)
    actual = {name: digest_value for name, digest_value in _hashes(directory).items()
              if name not in ('freeze.json', 'freeze.sha256')}
    if actual != expected:
        raise ValueError('Execution archive file set changed')
    for name, hashes in frozen['trees'].items():
        if _hashes(_safe(directory, name)) != hashes:
            raise ValueError('Frozen tree differs: ' + name)
    design = read(directory / 'protocol-design/protocol.json')
    if (sha(directory / 'protocol-design/freeze.json') != frozen['design_freeze_sha256']
            or design != frozen['protocol'] or design['entries'] != frozen['entries']
            or design['schedule'] != frozen['schedule']):
        raise ValueError('Execution tasks or design identity differ')
    if read(directory / 'environment.json') != frozen['environment']:
        raise ValueError('Environment evidence differs from freeze')
    source_check = dict(status='not_checked', matches=None)
    if root is not None:
        root = Path(root).resolve()
        mismatches = [name for name, expected_sha in frozen['source_origin_files'].items()
                      if not _safe(root, name).is_file() or sha(_safe(root, name)) != expected_sha]
        source_check = dict(status='pass' if not mismatches else 'mismatch',
                            matches=not mismatches, mismatches=mismatches)
    environment_check = dict(status='not_checked', matches=None)
    if check_current_environment:
        source_root = Path(root or frozen['source_origin'])
        current = observe_environment(source_root, protocol_directory=directory / 'protocol-design',
            python_executable=python_executable or frozen['python_executable'],
            codex_executable=codex_executable or frozen['cli'],
            docker_executable=docker_executable or frozen['docker'],
            configured_model=frozen['model'], configured_reasoning_effort=frozen['model_reasoning_effort'])
        if current['environment_gaps']:
            environment_check = dict(status='unknown', matches=None,
                                     gaps=current['environment_gaps'], current_observation=current)
        else:
            old_values, new_values = _comparable_environment(frozen['environment']), _comparable_environment(current)
            mismatches = [key for key in old_values if old_values[key] != new_values[key]]
            environment_check = dict(status='pass' if not mismatches else 'mismatch',
                                     matches=not mismatches, mismatches=mismatches,
                                     current_observation=current)
    return dict(schema=SCHEMA, freeze_sha256=digest, archive_integrity_verified=True,
                frozen_file_count=len(expected), counts=frozen['counts'],
                current_source_verification=source_check,
                current_environment_verification=environment_check,
                frozen_environment_preflight_pass=frozen['environment_preflight_pass'],
                frozen_environment_gaps=frozen['environment_gaps'],
                execution_authorized=False, heldout_ready=False,
                model_availability_verified=False, new_c0_development_checks_passed=False,
                new_model_calls=0, new_simulations=0, new_sampling_calls=0)
