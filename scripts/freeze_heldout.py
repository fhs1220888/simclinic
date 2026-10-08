"""Freeze a controlled field-selection ablation before any held task is run."""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_full_agent import REQUIREMENT, IMAGE, CODEX, CORPUS
from simclinic.evaluation.heldout import TASKS, DEVELOPMENT, hashes, requirement, create_reference
from simclinic.core.report import write_json


def function_text(text):
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == '_guess_primary_field')
    return ast.get_source_segment(text, node)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    observed = ROOT / 'integration/foam_agent_v210/native/observed'
    manifest = json.loads((observed/'candidate-manifest.json').read_text())
    for name, digest in manifest['source_files'].items():
        if hashlib.sha256((observed/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Candidate differs from previously validated source')
    common = out/'common-runtime'
    shutil.copytree(ROOT/'simclinic', common/'simclinic', ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('run_heldout_run.py', 'run_heldout_batch.py', 'report_heldout.py', 'freeze_heldout.py'):
        path = common/'scripts'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/'scripts'/name, path)
    shutil.copytree(CORPUS, out/'corpus', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(observed.parent/'requirements.lock', out/'requirements.lock')
    source_manifests = {}
    for arm in ('baseline', 'candidate'):
        destination = out/'variants'/arm
        shutil.copytree(observed, destination, ignore=shutil.ignore_patterns('__pycache__'))
        (destination/'candidate-manifest.json').rename(destination/'origin-observed-manifest.json')
        if arm == 'baseline':
            path = destination/'src/nodes/visualization_node.py'
            text = path.read_text()
            origin = observed.parent/'patched/src/nodes/visualization_node.py'
            text = text.replace(function_text(text), function_text(origin.read_text()), 1)
            path.write_text(text)
        source_manifests[arm] = hashes(destination)
    changed = [name for name in source_manifests['baseline'] if source_manifests['baseline'][name] != source_manifests['candidate'][name]]
    if changed != ['src/nodes/visualization_node.py']:
        raise ValueError('Arms differ outside the predeclared policy')
    filename = changed[0]
    a = (out/'variants/baseline'/filename).read_text()
    b = (out/'variants/candidate'/filename).read_text()
    if a.replace(function_text(a), '<POLICY>') != b.replace(function_text(b), '<POLICY>'):
        raise ValueError('Source differs outside the selected function')
    (out/'arm-difference.patch').write_text(''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
        fromfile='baseline/'+filename, tofile='candidate/'+filename)))
    task_manifests = {}
    for spec in [DEVELOPMENT] + TASKS:
        directory = out/'tasks'/spec['id']
        create_reference(CORPUS/'reference-case', directory/'acceptance-reference', spec)
        write_json(directory/'spec.json', spec)
        (directory/'requirement.txt').write_text(requirement(REQUIREMENT, spec))
        task_manifests[spec['id']] = hashes(directory)
    # Deterministic counterbalancing: three tasks x three repeats x two arms.
    schedule = []
    for repeat in range(1, 4):
        for index, spec in enumerate(TASKS):
            arms = ('baseline', 'candidate') if (repeat+index) % 2 else ('candidate', 'baseline')
            for arm in arms:
                schedule.append(dict(id=f"{spec['id']}-r{repeat}-{arm}", task=spec['id'], repeat=repeat, arm=arm))
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
    model = 'gpt-6.1-sol'
    frozen = dict(schema_version='1.0', frozen_at=datetime.now(timezone.utc).isoformat(),
        experiment='explicit-render-field-selection-ablation-v1',
        agent_commit=manifest['base_commit'], model=model, model_reasoning_effort='high',
        cli=CODEX, cli_version=subprocess.check_output([CODEX, '--version'], text=True).strip(),
        cli_sha256=hashlib.sha256(Path(CODEX).read_bytes()).hexdigest(),
        image=IMAGE, image_id=image['Id'], platform='linux/amd64', cpus=2, memory='2g', network='none',
        python=sys.version, executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
        common_runtime=hashes(common), corpus=hashes(out/'corpus'), tasks=task_manifests,
        variants=source_manifests, requirements_sha256=hashlib.sha256((out/'requirements.lock').read_bytes()).hexdigest(),
        arm_difference_sha256=hashlib.sha256((out/'arm-difference.patch').read_bytes()).hexdigest(),
        difference=dict(function='_guess_primary_field', baseline='Original pressure-first heuristic from patched v2.1.0',
            candidate='Explicit requested-field selection from validated observed candidate',
            shared='All other native source, model/RAG, process/runtime, provenance, task gate and thresholds'),
        reference_leakage_policy='Only development corpus is retrievable. Held acceptance dictionaries are never passed to the model.',
        schedule=schedule, planned_runs=18, review_limit=2, model_call_limit=32,
        model_render_fallback_limit=0, solver_timeout_seconds=180,
        batch_model_token_limit=5000000, includes_development_usage=True,
        no_reliable_model_seed=True, backend_snapshot_unavailable=True,
        analysis='Exploratory descriptive study; no significance/generalization claim; no fault injection.',
        change_policy='No edits after freeze; software faults require a new batch while retaining this one.')
    write_json(out/'freeze.json', frozen)
    (out/'freeze.sha256').write_text(hashlib.sha256((out/'freeze.json').read_bytes()).hexdigest()+'\n')
    print('Frozen before held runs:', out, flush=True)


if __name__ == '__main__':
    main()
