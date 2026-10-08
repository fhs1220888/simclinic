"""Freeze a prospective content-contract study, without executing held tasks."""
import argparse
from datetime import datetime,timezone
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_full_agent import IMAGE,CODEX,CORPUS,REQUIREMENT
from simclinic.evaluation.heldout import hashes,create_reference,requirement,DEVELOPMENT,TASKS as PREVIOUS
from simclinic.evaluation.content_study import TASKS,INFERENCE_SCOPE
from simclinic.core.report import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():raise ValueError('Do not overwrite a frozen batch')
    for spec in TASKS:
        if any(all(spec[k]==old[k] for k in ('mean_velocity','nx','ny')) for old in PREVIOUS+[DEVELOPMENT]):
            raise ValueError('Previously used task parameters cannot be an independent held task')
    manifests = {}
    for arm,name in (('baseline','observed'),('candidate','content')):
        source = ROOT/'integration/foam_agent_v210/native'/name
        manifest = json.loads((source/'candidate-manifest.json').read_text())
        for file,digest in manifest['source_files'].items():
            if hashlib.sha256((source/file).read_bytes()).hexdigest()!=digest:
                raise ValueError('Source changed since candidate build: '+file)
        manifests[arm] = manifest
    helper_sha = hashlib.sha256((ROOT/'simclinic/input_content.py').read_bytes()).hexdigest()
    if helper_sha != manifests['candidate']['runtime_requirements']['input_content_sha256']:
        raise ValueError('Content helper changed since candidate build')
    image = json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    out.mkdir(parents=True)
    common = out/'common-runtime'
    shutil.copytree(ROOT/'simclinic',common/'simclinic',ignore=shutil.ignore_patterns('__pycache__'))
    scripts = common/'scripts';scripts.mkdir()
    for name in ('run_heldout_run.py','run_heldout_batch.py','report_heldout.py','freeze_content_study.py','report_heldout_attempts.py'):
        shutil.copy2(ROOT/'scripts'/name,scripts/name)
    shutil.copytree(CORPUS,out/'corpus',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT/'integration/foam_agent_v210/native/requirements.lock',out/'requirements.lock')
    variants = {}
    for arm,name in (('baseline','observed'),('candidate','content')):
        destination = out/'variants'/arm
        shutil.copytree(ROOT/'integration/foam_agent_v210/native'/name,destination,
                        ignore=shutil.ignore_patterns('__pycache__'))
        # Origin metadata is shared; actual arm hashes are authoritative below.
        (destination/'candidate-manifest.json').unlink()
        write_json(destination/'origin-observed-manifest.json',manifests['baseline'])
        variants[arm] = hashes(destination)
    changed = [n for n in variants['baseline'] if variants['baseline'][n]!=variants['candidate'][n]]
    if changed != ['src/services/input_writer.py']:
        raise ValueError('Unexpected source difference: '+str(changed))
    filename = changed[0]
    before = (out/'variants/baseline'/filename).read_text()
    after = (out/'variants/candidate'/filename).read_text()
    (out/'arm-difference.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
        fromfile='baseline/'+filename,tofile='candidate/'+filename)))
    tasks = {}
    for spec in [DEVELOPMENT]+TASKS:
        path = out/'tasks'/spec['id']
        create_reference(CORPUS/'reference-case',path/'acceptance-reference',spec)
        write_json(path/'spec.json',spec)
        (path/'requirement.txt').write_text(requirement(REQUIREMENT,spec))
        tasks[spec['id']] = hashes(path)
    schedule = []
    for repeat in (1,2,3):
        for index,spec in enumerate(TASKS):
            arms = ('baseline','candidate') if (repeat+index)%2 else ('candidate','baseline')
            for arm in arms:
                schedule.append(dict(id=f"{spec['id']}-r{repeat}-{arm}",task=spec['id'],repeat=repeat,arm=arm))
    frozen = dict(schema_version='1.0',frozen_at=datetime.now(timezone.utc).isoformat(),
        experiment='initial-file-content-contract-v1',agent_name='Controlled Foam-Agent file-content contract study',
        agent_commit=manifests['baseline']['base_commit'],model='gpt-6.1-sol',model_reasoning_effort='high',
        cli=CODEX,cli_version=subprocess.check_output([CODEX,'--version'],text=True).strip(),
        cli_sha256=hashlib.sha256(Path(CODEX).read_bytes()).hexdigest(),
        image=IMAGE,image_id=image['Id'],platform='linux/amd64',cpus=2,memory='2g',network='none',
        python=sys.version,executable=sys.executable,
        installed_dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines(),
        common_runtime=hashes(common),corpus=hashes(out/'corpus'),tasks=tasks,variants=variants,
        requirements_sha256=hashlib.sha256((out/'requirements.lock').read_bytes()).hexdigest(),
        arm_difference_sha256=hashlib.sha256((out/'arm-difference.patch').read_bytes()).hexdigest(),
        content_helper_sha256=helper_sha,inference_scope=INFERENCE_SCOPE,
        difference=dict(baseline='Existing parse_context in initial_write',
            candidate='decode_foamfile_response in initial_write; exact target identity, one JSON decode, header check',
            shared='U field selection, prompts, reviewer/rewrite, all other source, model/RAG, execution, provenance and acceptance'),
        reference_leakage_policy='Only unchanged D0 development corpus retrievable. New held dictionaries and results never passed to the model.',
        excluded_development_data=['D0','H1','H2','H3','Historical H2-r3 failure and content-contract replay'],
        development_quality_expected={'baseline':True,'candidate':True},
        schedule=schedule,planned_runs=18,review_limit=2,model_call_limit=32,model_render_fallback_limit=0,
        solver_timeout_seconds=180,batch_model_token_limit=5000000,includes_development_usage=True,
        primary_outcome='Full planned-workflow task quality; keep failures and missing evidence in denominator.',
        secondary_outcomes=['First actual Allrun failure (no attempt is unknown, not pass)',
            'All execution attempts and failures, InputContentError before execution',
            'Wrong success claims, invalid retries, missing evidence',
            'All-workflow consumption including recovery; qualified same-task pairs only for consumption comparison'],
        no_reliable_model_seed=True,backend_snapshot_unavailable=True,
        analysis='Exploratory descriptive study. No fault injection or archived-response replay in held runs. Zero observed format failures may leave effect undetermined.',
        change_policy='No edits after freeze; software failures require a new batch, preserve all old attempts.')
    write_json(out/'freeze.json',frozen)
    (out/'freeze.sha256').write_text(hashlib.sha256((out/'freeze.json').read_bytes()).hexdigest()+'\n')
    (out/'READY.md').write_text('# 文件内容接口评估已冻结，尚未执行\n\n'
        '此目录没有 progress.json 或真实运行结果。执行冻结的 common-runtime/scripts/run_heldout_batch.py 后，'
        '先进行两组 D0 协议检查，再执行全部 18 次新保留任务。已知故障重放排除在外。\n')
    print('Frozen; no model or simulation calls:',out)


if __name__=='__main__':main()
