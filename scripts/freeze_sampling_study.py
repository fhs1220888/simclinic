"""Freeze equal-source policy arms before real development or held runs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_full_agent import IMAGE, CODEX, CORPUS, REQUIREMENT
from simclinic.core.report import write_json
from simclinic.evaluation.heldout import hashes, create_reference, requirement, DEVELOPMENT
from simclinic.evaluation.sampling_study import TASKS, POLICIES, INFERENCE_SCOPE, schedule, validate_new_tasks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--predecessor', type=Path,
                        help='Optional stopped batch; preserve original failures and record a new protocol')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise ValueError('Do not overwrite an existing freeze')
    validate_new_tasks()
    predecessor = None
    if args.predecessor:
        from simclinic.evaluation.sampling_study import verify_sampling_freeze
        original = args.predecessor.resolve()
        previous, digest = verify_sampling_freeze(original)
        progress = json.loads((original / 'progress.json').read_text())
        if progress['status'] not in ('usage_unknown', 'development_contract_failed') or progress['freeze_sha256'] != digest:
            raise ValueError('Expected a preserved stopped batch')
        predecessor = dict(directory=str(original), freeze_sha256=digest, status=progress['status'],
                           reason='CLI request failed before solve; preserve diagnostics and unknown usage; add shared coordinator idle-sleep assertion and distinct tasks')
    native = ROOT / 'integration/foam_agent_v210/native/content'
    manifest = json.loads((native / 'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((native / name).read_bytes()).hexdigest() != sha:
            raise ValueError('Native source changed: ' + name)
    if hashlib.sha256((ROOT / 'simclinic/input_content.py').read_bytes()).hexdigest() != manifest['runtime_requirements']['input_content_sha256']:
        raise ValueError('Content helper changed')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
    out.mkdir(parents=True)
    common = out / 'common-runtime'
    shutil.copytree(ROOT / 'simclinic', common / 'simclinic', ignore=shutil.ignore_patterns('__pycache__'))
    (common / 'scripts').mkdir()
    for name in ('freeze_sampling_study.py', 'run_sampling_study_run.py', 'run_sampling_study_batch.py',
                 'report_heldout.py', 'report_sampling_study.py', 'verify_sampling_study.py'):
        shutil.copy2(ROOT / 'scripts' / name, common / 'scripts' / name)
    shutil.copytree(CORPUS, out / 'corpus', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(native.parent / 'requirements.lock', out / 'requirements.lock')
    variants = {}
    for arm in POLICIES:
        shutil.copytree(native, out / 'variants' / arm, ignore=shutil.ignore_patterns('__pycache__'))
        variants[arm] = hashes(out / 'variants' / arm)
    if variants['baseline'] != variants['candidate']:
        raise ValueError('Native sources differ')
    difference = ('--- baseline/completion-policy\n+++ candidate/completion-policy\n@@ -1 +1 @@\n'
                  '-HeldoutCompletion\n+GatedHeldoutCompletion\n')
    (out / 'arm-difference.patch').write_text(difference)
    tasks = {}
    for spec in [DEVELOPMENT] + TASKS:
        path = out / 'tasks' / spec['id']
        create_reference(CORPUS / 'reference-case', path / 'acceptance-reference', spec)
        write_json(path / 'spec.json', spec)
        (path / 'requirement.txt').write_text(requirement(REQUIREMENT, spec))
        tasks[spec['id']] = hashes(path)
    frozen = dict(schema_version='1.0', frozen_at=datetime.now(timezone.utc).isoformat(),
        experiment='registered-sampling-gate-v1', agent_name='Controlled Foam-Agent sampling policy study',
        agent_commit=manifest['base_commit'], model='gpt-6.1-sol', model_reasoning_effort='high',
        cli=CODEX, cli_version=subprocess.check_output([CODEX, '--version'], text=True).strip(),
        cli_sha256=hashlib.sha256(Path(CODEX).read_bytes()).hexdigest(),
        image=IMAGE, image_id=image['Id'], platform='linux/amd64', cpus=2, memory='2g', network='none',
        python=sys.version, executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
        common_runtime=hashes(common), corpus=hashes(out / 'corpus'), tasks=tasks, variants=variants,
        requirements_sha256=hashlib.sha256((out / 'requirements.lock').read_bytes()).hexdigest(),
        arm_difference_sha256=hashlib.sha256(difference.encode()).hexdigest(),
        arm_difference_kind='Policy selector declaration, not a native source patch',
        completion_policies=POLICIES, inference_scope=INFERENCE_SCOPE,
        difference=dict(baseline='HeldoutCompletion', candidate='GatedHeldoutCompletion',
                        shared='Identical content native source, model/prompts/RAG, U rules, reviewer, resources and original acceptance'),
        reference_leakage_policy='Only unchanged D0 corpus retrievable; held reference inputs/results never provided to the model',
        excluded_development_data=['D0', 'H1', 'H2', 'H3', 'I1', 'I2', 'I3', 'J1', 'J2', 'J3', 'K1', 'K2', 'K3', 'All archived replies and sampling gate regressions'],
        predecessor=predecessor,
        idle_sleep_guard=dict(command='/usr/bin/caffeinate',
                              sha256=hashlib.sha256(Path('/usr/bin/caffeinate').read_bytes()).hexdigest(),
                              policy='Temporary -i -w coordinator PID assertion, identical between arms; no persistent power settings'),
        development_quality_expected=dict(baseline=True, candidate=True), schedule=schedule(), planned_runs=18,
        review_limit=2, model_call_limit=32, model_render_fallback_limit=0, solver_timeout_seconds=180,
        batch_model_token_limit=5000000, development_model_token_limit=1000000,
        reserve_per_run_model_tokens=450000, includes_development_usage=True,
        primary_outcome='Full task quality under unchanged execution/numerical/reference/input/U criteria; failures and missing evidence retained',
        secondary_outcomes=['Verified fatal-log sampling events and their tool outcome/error category',
                            'Declared output evidence status; absent archive evidence stays unknown',
                            'Wrong success claims, invalid retries, attempts and evidence missing',
                            'Solver time and model usage; compare consumption only in quality-qualified pairs'],
        branch_trigger_rule='Candidate node invocation is distinct from a natural failure branch; report both separately',
        no_natural_failure_rule='Zero qualifying natural sampling failures leaves diagnostic benefit frequency undetermined',
        no_reliable_model_seed=True, backend_snapshot_unavailable=True,
        analysis='Exploratory descriptive comparison of parameter variants in one channel; no fault injection or reply replay in held runs',
        change_policy='No tuning or code edits inside freeze. Failed development prevents held launch; preserve failed batches',
        stages='Development and heldout require separate coordinator invocations; no automatic heldout launch')
    write_json(out / 'freeze.json', frozen)
    (out / 'freeze.sha256').write_text(hashlib.sha256((out / 'freeze.json').read_bytes()).hexdigest() + '\n')
    (out / 'READY.md').write_text('# 采样策略评估已冻结\n\n先运行冻结 coordinator 的 --stage development。两组 D0 完整模型链通过后，另用 --stage heldout。L1/L2/L3 尚未执行；历史 H/I/J/K 与开发回归均排除。临时防空闲睡眠保护覆盖整个协调器生命周期。\n')
    print('Frozen; no model/simulation calls:', out, flush=True)


if __name__ == '__main__':
    main()
