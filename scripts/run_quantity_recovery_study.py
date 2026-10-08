"""Freeze and run fresh full-model Couette recovery comparisons, serial once only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simclinic.core.report import write_json,write_report
from simclinic.core.record import load_record
from simclinic.evaluation.heldout import hashes,verify_hashes
from simclinic.evaluation.quantity_recovery_study import schedule,budget_allows,outcomes
from simclinic.evaluation.sampling_challenge import sampling_challenge
from simclinic.evaluation.idle_guard import start_idle_guard
from simclinic.domains.openfoam.reference_tasks import create_reference,task,ReferenceTaskCompletion,reference_measurements,stationary_fields
from simclinic.domains.openfoam.quantity_inputs import (POLICY, compare_case, quantity_task as task, QuantityReferenceCompletion as ReferenceTaskCompletion, RecoveringQuantityReferenceCompletion as RecoveringReferenceCompletion)
from simclinic.domains.openfoam.sampling_diagnostics import diagnose_sampling
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.domains.openfoam.acceptance import solver_measurements
from simclinic.checks.behavior import matches,retries
from simclinic.cli import get_report
from simclinic.session import SessionRecorder,plain
from simclinic.backends.codex_cli import CodexCLI
from simclinic.retrieval import LexicalReferences
from simclinic.result_guard import read_verified_result


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def build_corpus(destination,template,validated):
    """Only C0 coarse and existing real command help enter shared retrieval."""
    destination.mkdir()
    reference=create_reference(template/'reference-case',destination/'reference-case',schedule()[0]['spec'])
    groups={name:sorted(p.name for p in (reference/name).iterdir()) for name in ('0','constant','system')}
    structure='\n'.join('<dir>directory name: '+name+'. File names in this directory: ['+', '.join(files)+']</dir>' for name,files in groups.items())
    content='<directory_structure>\n'+structure+'\n</directory_structure>\n'
    for name,files in groups.items():
        for filename in files:content+='<file path="'+name+'/'+filename+'">\n'+(reference/name/filename).read_text()+'\n</file>\n'
    metadata=dict(case_name='couette',case_solver='simpleFoam',case_domain='incompressible',case_category='laminar',dir_structure=structure)
    doc=dict(id='validated-couette-C0',index='couette moving wall simpleFoam incompressible laminar steady velocity pressure',full_content=content,**metadata)
    allrun=dict(id='couette-allrun',index='couette simpleFoam blockMesh checkMesh incompressible',full_content=(reference/'Allrun').read_text(),**metadata)
    help_docs=read(template/'corpus.json')['collections']['openfoam_command_help']
    write_json(destination/'corpus.json',dict(schema_version='1.0',synthetic=False,retrieval_method='lexical-overlap-v1',
        source='Authored Couette C0 with archived CFD validation; unchanged actual Foundation10 command help',
        validated_reference_archive=str(validated),heldout_reference_or_outputs_in_corpus=False,
        collections=dict(openfoam_tutorials_structure=[doc],openfoam_tutorials_details=[doc],
                         openfoam_allrun_scripts=[allrun],openfoam_command_help=help_docs)))
    (destination/'raw').mkdir()
    write_json(destination/'raw/openfoam_case_stats.json',dict(case_domain=['incompressible'],case_category=['laminar'],case_solver=['simpleFoam']))
    (destination/'raw/openfoam_commands.txt').write_text('blockMesh\ncheckMesh\nsimpleFoam\n')


def freeze(batch):
    from scripts.run_full_agent import IMAGE,CODEX,CORPUS
    if batch.exists():raise ValueError('Preserve existing freeze; choose fresh directory')
    validated=ROOT/'real-runs/quantity-input-development-20261006'
    vf=read(validated/'freeze.json'); verification=read(validated/'archive-verification.json')
    if read(validated/'progress.json')['status']!='complete' or verification['freeze_sha256']!=sha(validated/'freeze.json') or not verification['all_expected_controls_verified'] or verification['totals']['solver_runs']!=6 or verification['totals']['model_calls']!=0:
        raise ValueError('Verified six quantity controls required')
    if sha(validated/'freeze.json')!=(validated/'freeze.sha256').read_text().strip(): raise ValueError('Development freeze changed')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('definitions','definitions')):verify_hashes(validated/folder,vf[key])
    for entry in vf['entries']:
        out=validated/'runs'/entry['id']; record=load_record(out/'record.json',out)
        if not read(out/'summary.json')['development_contract_met']: raise ValueError('Development outcome changed')
        for artifact in record.data['artifacts']:
            if sha(out/artifact['path'])!=artifact['sha256']: raise ValueError('Development evidence changed')
    for name in ('simclinic/domains/openfoam/reference_tasks.py','simclinic/domains/openfoam/quantity_inputs.py','simclinic/domains/openfoam/sampling_recovery.py','simclinic/evaluation/sampling_challenge.py'):
        if sha(ROOT/name)!=sha(validated/'common-runtime'/name):raise ValueError('Validated shared matcher or candidate changed')
    previous=ROOT/'real-runs/full-model-recovery-study-20261005'
    if sha(previous/'results.json')!=vf['historical_primary_results_sha256']: raise ValueError('Historical primary scores changed')
    native=ROOT/'integration/foam_agent_v210/native/content';manifest=read(native/'candidate-manifest.json')
    for name,expected in manifest['source_files'].items():
        if sha(native/name)!=expected:raise ValueError('Native source changed')
    if sha(ROOT/'simclinic/input_content.py')!=manifest['runtime_requirements']['input_content_sha256']:raise ValueError('Input helper changed')
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    batch.mkdir(parents=True);common=batch/'common-runtime'
    shutil.copytree(ROOT/'simclinic',common/'simclinic',ignore=shutil.ignore_patterns('__pycache__'))
    (common/'scripts').mkdir();shutil.copy2(Path(__file__),common/'scripts'/Path(__file__).name)
    shutil.copytree(native,batch/'native',ignore=shutil.ignore_patterns('__pycache__'))
    build_corpus(batch/'corpus',Path(CORPUS),validated)
    entries=schedule()
    for e in entries:
        definition=batch/'definitions'/e['id']
        create_reference(Path(CORPUS)/'reference-case',definition/'reference',e['spec'])
        write_json(definition/'spec.json',e['spec']);write_json(definition/'task.json',task(e['spec']))
    f=dict(experiment='full-model-quantity-policy-recovery-study-v2',frozen_at=now(),entries=entries,
        schedule=[e['id'] for e in entries],reference_development_sha256=sha(validated/'freeze.json'),
        reference_development_review_sha256=sha(validated/'archive-verification.json'), input_matching_policy=POLICY, historical_primary_results_sha256=vf['historical_primary_results_sha256'],
        model='gpt-6.1-sol',model_reasoning_effort='high',cli=CODEX,cli_sha256=sha(CODEX),
        cli_version=subprocess.check_output([CODEX,'--version'],text=True).strip(),
        image=IMAGE,image_id=image['Id'],python=sys.version,
        installed_dependencies=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines(),
        agent_commit=manifest['base_commit'],common_runtime=hashes(common),native=hashes(batch/'native'),
        corpus=hashes(batch/'corpus'),definitions=hashes(batch/'definitions'),
        model_call_limit=32,review_limit=2,model_render_fallback_limit=0,solver_timeout_seconds=180,
        sampler_timeout_seconds=120,token_limit=7500000,development_token_limit=1500000,reserve_per_run_model_tokens=450000,
        resources=dict(cpus=2,memory='2g',network='none',platform='linux/amd64'),
        idle_sleep_guard=dict(command='/usr/bin/caffeinate',sha256=sha('/usr/bin/caffeinate')),
        shared_evaluator_change='Both arms use prevalidated constant-nu optional-correct-dimensions literal matching; all other input rules, quantities and quality thresholds unchanged; historical scores not regraded',
        candidate_difference='Only one fresh verified sampler workspace retry for typed sampling_missing_output; solver/numerics/acceptance/upstream identical',
        stages='4 fresh full-model healthy C0 development runs must all pass; then 24 held parameter tasks: C3/C4 x healthy/transient_missing x 3 repeats x 2 arms',
        challenge='Remove one newly generated profile after successful first sampler only; same rule both arms; actual execution logs and registered removed bytes retained',
        stopping='No resume/selective reruns. Stop development quality failure, infrastructure/collection failure, unknown usage, failed model transport or reserve boundary. Honest held task failures remain outcomes and do not alone stop batch',
        scoring='Separate healthy from injected scenarios. All predeclared held runs in denominator; missing quality never success. Quality gate precedes time/token comparisons; report all attempts, false completion and ineffective retries',
        heldout_scope='New parameter/mesh tasks of validated Couette mechanism, not new physics. C0/F0/C1/C2 and old H/I/J/K/L excluded. No solved held reference or archived replies in model retrieval',
        limits='Small explicitly injected study; not natural failure rate, statistical significance, broad CFD applicability or overall optimization',
        optimization_effect_proven=False,model_cost=None)
    write_json(batch/'freeze.json',f);(batch/'freeze.sha256').write_text(sha(batch/'freeze.json')+'\n')
    print('Frozen 4 development + 24 held runs; zero new model/solver calls:',batch)


def verify_freeze(batch):
    f=read(batch/'freeze.json');digest=sha(batch/'freeze.json')
    if digest!=(batch/'freeze.sha256').read_text().strip() or f['entries']!=schedule() or f['schedule']!=[e['id'] for e in schedule()]:raise ValueError('Protocol changed')
    for folder,key in (('common-runtime','common_runtime'),('native','native'),('corpus','corpus'),('definitions','definitions')):verify_hashes(batch/folder,f[key])
    return f,digest


def runtime_check(f):
    if sys.version!=f['python'] or subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines()!=f['installed_dependencies'] or sha(f['cli'])!=f['cli_sha256']:raise ValueError('Runtime/backend changed')
    image=json.loads(subprocess.check_output(['docker','image','inspect',f['image']],text=True))[0]
    if image['Id']!=f['image_id']:raise ValueError('Image changed')


def run(batch,rid):
    f,digest=verify_freeze(batch);p=read(batch/'progress.json')
    active=[r for r in p['runs'] if r['status']=='running']
    if ROOT!=batch/'common-runtime' or p['status']!='running' or p['freeze_sha256']!=digest or len(active)!=1 or active[0]['id']!=rid:raise ValueError('Archived coordinator registration required')
    runtime_check(f);e=next(e for e in f['entries'] if e['id']==rid)
    out=batch/'runs'/rid;out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(batch/'native',out/'source-package');sys.path.insert(0,str(out/'source-package/src'))
    from main import create_foam_agent_graph,initialize_state
    from config import Config
    from services import configure_llm
    from utils import configure_retriever
    from logger import close_logging
    definition=batch/'definitions'/rid;declared=read(definition/'task.json');spec=read(definition/'spec.json')
    environment=dict(freeze_sha256=digest,image=f['image'],model=f['model'],distribution='OpenFOAM Foundation',version='10',solver='simpleFoam',resources=f['resources'],graph_entrypoint='planner',arm=e['arm'])
    recorder=SessionRecorder(out,declared,environment,dict(name='Foam-Agent Couette recovery comparison',commit=f['agent_commit'],full_agent_workflow=True))
    recorder.data.update(heldout=e['stage']=='heldout',fault_injection=e['scenario']!='healthy')
    backend=CodexCLI(out/'model-calls',f['model'],executable=f['cli'],max_calls=f['model_call_limit'],observer=recorder.model)
    configure_llm(backend);configure_retriever(LexicalReferences(batch/'corpus/corpus.json'))
    os.environ.update(SIMCLINIC_DOCKER_IMAGE=f['image'],SIMCLINIC_OBSERVED_EXECUTION='1',LANGCHAIN_TRACING_V2='false')
    config=Config(case_dir=str(out/'case'),database_path=str(batch/'corpus'),max_loop=f['review_limit'],max_time_limit=f['solver_timeout_seconds'],model_version=f['model'],searchdocs=1,recursion_limit=30)
    config.visualization_max_loop=f['model_render_fallback_limit']
    write_json(out/'environment.json',dict(**environment,acceptance_reference_in_model_context=False))
    objective=declared['objective'];(out/'requirement.txt').write_text(objective)
    policy=(RecoveringReferenceCompletion if e['arm']=='candidate' else ReferenceTaskCompletion)(recorder,definition/'reference',f['image'],spec)
    result,failure={},None
    try:
        initial=initialize_state(objective,config,llm_service=backend)
        graph=create_foam_agent_graph(observer=recorder.observe,completion_policy=policy).compile()
        with sampling_challenge(recorder,e['scenario']):result=graph.invoke(initial,config={'recursion_limit':30})
        write_json(out/'graph-result.json',plain(result));recorder.finish(completed='completion_claim' in result)
    except Exception as exc:
        failure=dict(type=type(exc).__name__,message=str(exc));write_json(out/'failure.json',failure);recorder.finish(completed=False)
    finally:
        close_logging();write_json(out/'model-usage.json',backend.get_statistics())
    record=load_record(out/'record.json',out);audit=get_report(record);write_report(audit,out/'audit')
    write_json(out/'sampling-diagnostic.json',diagnose_sampling(record))
    acceptance=result.get('acceptance_report',{});claim=result.get('completion_claim')
    quality=bool(acceptance.get('accepted')) and bool(claim and claim.get('success'))
    independently_met=acceptance.get('error') is None and all(matches(acceptance.get('values',{}).get(c['id']),c) is True for c in declared['acceptance'])
    s=dict(**e,quality_met=quality,false_completion=bool(claim and claim.get('success') and not independently_met),
        completion=claim,acceptance=acceptance,metrics=record.data['metrics'],model_usage=backend.get_statistics(),
        model_tokens=backend.get_statistics()['total_tokens'],attempts=len(record.data['attempts']),
        sample_calls=sum(ev.get('operation')=='sample' for ev in record.data['events']),
        retry_finding=retries(record)['status']=='finding',recovery_attempts=getattr(policy,'recovery_attempts',0),
        coverage=record.data['coverage'],failure=failure,optimization_effect_proven=False)
    write_json(out/'summary.json',s);print(json.dumps(s,ensure_ascii=False),flush=True)
    return 0 if failure is None else 2


def report(batch):
    f,digest=verify_freeze(batch);p=read(batch/'progress.json');rows=[]
    lines=['# 完整模型有限采样恢复比较','','正常与明确丢失注入分开；开发运行排除在保留分母之外。','',
           '| 运行 | 完整质量 | 错误完成 | sample 次数 | 无效重试 | token | 工作流秒 |','| --- | --- | --- | --- | --- | --- | --- |']
    for e in p['runs']:
        file=batch/'runs'/e['id']/'summary.json'
        s=read(file) if file.is_file() else dict(**next(x for x in f['entries'] if x['id']==e['id']),quality_met=False,model_tokens=None)
        rows.append(s)
        lines.append(f"| {e['id']} | {s['quality_met']} | {s.get('false_completion')} | {s.get('sample_calls')} | {s.get('retry_finding')} | {s['model_tokens']} | {s.get('metrics',{}).get('wall_seconds')} |")
    scored=outcomes(rows,p['status']=='complete')
    pairs=[]
    for base in rows:
        if base['stage']!='heldout' or base['arm']!='baseline':continue
        candidates=[r for r in rows if r['stage']=='heldout' and r['arm']=='candidate' and (r['spec']['id'],r['repeat'],r['scenario'])==(base['spec']['id'],base['repeat'],base['scenario'])]
        if not candidates:continue
        cand=candidates[0];both=base['quality_met'] and cand['quality_met']
        differences={k:(cand.get('metrics',{}).get(k)-base.get('metrics',{}).get(k)) if both and cand.get('metrics',{}).get(k) is not None and base.get('metrics',{}).get(k) is not None else None for k in ('wall_seconds','solver_seconds','model_tokens')}
        pairs.append(dict(baseline=base['id'],candidate=cand['id'],both_quality_pass=both,differences_candidate_minus_baseline=differences))
    write_json(batch/'results.json',dict(freeze_sha256=digest,status=p['status'],rows=rows,pairs=pairs,scoring=scored,known_model_tokens=p['known_model_tokens'],model_tokens=p['model_tokens'],model_cost=None))
    lines+=['',f"批次状态：{p['status']}；已知 token：{p['known_model_tokens']}；完整 token：{p['model_tokens']}；费用未知。",'',json.dumps(scored,ensure_ascii=False,indent=2),'','注入恢复只支持预声明缺失条件下的结论。不得解释为自然故障率、统计显著性或总体优化。']
    (batch/'report.md').write_text('\n'.join(lines)+'\n')


def coordinate(batch):
    f,digest=verify_freeze(batch)
    if ROOT!=batch/'common-runtime' or (batch/'progress.json').exists():raise ValueError('Fresh archived coordinator only; no resume')
    runtime_check(f);guard=start_idle_guard(batch,f,'comparison')
    write_json(batch/'idle-assertion-active.json',dict(guard_pid=guard.pid,assertions=subprocess.check_output(['/usr/bin/pmset','-g','assertions'],text=True)))
    p=dict(status='running',freeze_sha256=digest,started_at=now(),runs=[],known_model_tokens=0,development_tokens=0,model_tokens=0)
    path=batch/'progress.json';write_json(path,p)
    for e in f['entries']:
        if not budget_allows(p['known_model_tokens'],p['development_tokens'],e['stage'],f):p['status']='budget_boundary';break
        row=dict(id=e['id'],status='running',started_at=now());p['runs'].append(row);write_json(path,p)
        log=batch/'execution-logs'/(e['id']+'.log');log.parent.mkdir(exist_ok=True)
        print('START',e['id'],flush=True)
        with log.open('x') as stream:proc=subprocess.run([sys.executable,str(Path(__file__)),'run','--batch',str(batch),'--id',e['id']],stdout=stream,stderr=subprocess.STDOUT)
        out=batch/'runs'/e['id'];s=read(out/'summary.json') if (out/'summary.json').exists() else {}
        meta=[read(m) for m in sorted((out/'model-calls').glob('*/metadata.json'))]
        known=sum(m['total_tokens'] for m in meta if m.get('total_tokens') is not None)
        usage_complete=bool(meta) and all(m.get('total_tokens') is not None for m in meta) and s.get('model_tokens')==known
        row.update(status='finished',ended_at=now(),exit_code=proc.returncode,quality_met=s.get('quality_met',False),known_model_tokens=known,model_tokens=known if usage_complete else None)
        p['known_model_tokens']+=known
        if e['stage']=='development':p['development_tokens']+=known
        p['model_tokens']=p['known_model_tokens'] if usage_complete else None
        if not usage_complete:p['status']='usage_unknown'
        elif proc.returncode or not (out/'record.json').is_file() or any(m.get('outcome')!='pass' for m in meta):p['status']='infrastructure_or_collection_failure'
        elif e['stage']=='development' and not row['quality_met']:p['status']='development_quality_failure'
        elif p['known_model_tokens']>f['token_limit'] or p['development_tokens']>f['development_token_limit']:p['status']='budget_boundary'
        write_json(path,p);report(batch);print('END',e['id'],p['status'],known,flush=True)
        if p['status']!='running':break
    else:p['status']='complete'
    p['ended_at']=now();write_json(path,p);report(batch)
    return 0 if p['status']=='complete' else 2


def verify(batch):
    f,digest=verify_freeze(batch);p=read(batch/'progress.json')
    if p['freeze_sha256']!=digest or p['status']!='complete' or [r['id'] for r in p['runs']]!=f['schedule']:raise ValueError('Complete fixed schedule required')
    totals=dict(artifacts=0,input_snapshots=0,model_calls=0,model_tokens=0);identities=set();last_end=None
    for row in p['runs']:
        if row['status']!='finished' or (last_end is not None and row['started_at']<last_end):raise ValueError('Unfinished/overlapping runs')
        last_end=row['ended_at']
    for e in f['entries']:
        out=batch/'runs'/e['id'];record=load_record(out/'record.json',out);s=read(out/'summary.json')
        if record.data['task']!=read(batch/'definitions'/e['id']/'task.json') or record.data['heldout']!=(e['stage']=='heldout') or record.data['fault_injection']!=(e['scenario']!='healthy'):raise ValueError('Task/scope changed')
        if read(out/'environment.json')['freeze_sha256']!=digest:raise ValueError('Wrong environment')
        verify_hashes(out/'source-package',f['native'])
        for a in record.data['artifacts']:
            if sha(out/a['path'])!=a['sha256']:raise ValueError('Evidence changed')
        totals['artifacts']+=len(record.data['artifacts'])
        for a in record.data['attempts']:
            if a['run_id'] in identities:raise ValueError('Reused result identity')
            identities.add(a['run_id']);native=out/'attempts'/a['run_id']/'native';m=read(native/'input-manifest.json')
            if hashes(native/'input-snapshot')!=m['files']:raise ValueError('Input snapshot changed')
            version='sha256:'+hashlib.sha256(json.dumps(m['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if version!=a['input_version'] or version!=m['input_version']:raise ValueError('Input identity changed')
            totals['input_snapshots']+=1
        meta=[read(m) for m in sorted((out/'model-calls').glob('*/metadata.json'))]
        if any(m.get('total_tokens') is None for m in meta):raise ValueError('Unknown usage')
        tokens=sum(m['total_tokens'] for m in meta)
        if tokens!=s['model_tokens'] or tokens!=record.data['metrics']['model_tokens'] or len(meta)!=s['model_usage']['total_calls']:raise ValueError('Usage mismatch')
        totals['model_calls']+=len(meta);totals['model_tokens']+=tokens
        if diagnose_sampling(record)!=read(out/'sampling-diagnostic.json'):raise ValueError('Diagnostic mismatch')
        samples=[ev for ev in record.data['events'] if ev.get('operation')=='sample']
        if len(samples)>(2 if e['arm']=='candidate' else 1):raise ValueError('Sampler retry budget exceeded')
        if len(samples)==2 and samples[1].get('retry_of')!=samples[0]['id']:raise ValueError('Retry lineage missing')
        for ev in record.data['events']:
            if ev.get('observation_type')=='explicit_sampling_challenge':
                intervention=json.loads(read_verified_result(record,ev['source_artifact'],ev['run_id'],ev['input_version']))
                removed=read_verified_result(record,intervention['payload_artifact'],ev['run_id'],ev['input_version'])
                if intervention['natural_failure'] is not False or intervention['original_reader_bytes_unchanged'] is not True or hashlib.sha256(removed).hexdigest()!=intervention['removed_sha256']:raise ValueError('Challenge integrity/label differs')
        if bool(s['completion'] and s['completion']['success'])!=s['quality_met']:raise ValueError('Quality claim mismatch')
        if s['completion']:
            claims=[(i,ev) for i,ev in enumerate(record.data['events']) if ev.get('claim_type')=='completion']
            if len(claims)!=1:raise ValueError('Wrong completion count')
            index,claim=claims[0];payload=read_verified_result(record,claim['source_artifact'],claim['run_id'],claim['input_version'])
            if json.loads(payload)['payload']['completion_claim']!=s['completion']:raise ValueError('Wrong completion evidence')
            assessment=s['acceptance'];criteria=record.data['task']['acceptance']
            if assessment['criteria']!=criteria or assessment['accepted']!=(assessment['error'] is None and all(matches(assessment['values'][c['id']],c) is True for c in criteria)):raise ValueError('Acceptance mismatch')
            measures=[(i,ev) for i,ev in enumerate(record.data['events']) if ev['kind']=='measurement']
            if len(measures)!=len(criteria) or any(i>=index or ev['value']!=assessment['values'][ev['criterion_id']] for i,ev in measures):raise ValueError('Measurement/claim order differs')
            context=record.data['attempts'][-1]
            checks,evidence=compare_case(out/context['input_snapshot'],batch/'definitions'/e['id']/'reference')
            verified=(all(v is True for v in checks.values()) if checks else False)
            if checks and not any(v is False for v in checks.values()) and any(v is None for v in checks.values()): verified=None
            source_snapshot=out/'case/.simclinic/native'/context['run_id']/'input-snapshot'
            verify_hashes(source_snapshot,read(out/'attempts'/context['run_id']/'native/input-manifest.json')['files'])
            for name,detail in evidence['files'].items(): detail['actual']=str(source_snapshot/name)
            if assessment.get('input_checks')!=checks or assessment.get('input_comparison')!=evidence or assessment['values']['task_inputs_verified'] is not verified: raise ValueError('Quantity input evidence differs')
            if s['quality_met']:
                context=record.data['attempts'][-1]
                snapshot=out/context['input_snapshot'];reference=batch/'definitions'/e['id']/'reference'
                if not all(v is True for v in compare_case(snapshot,reference)[0].values()): raise ValueError('Generated task inputs differ')
                solver=solver_measurements((out/'attempts'/context['run_id']/'logs/log.simpleFoam').read_text(),context['execution']['exit_code'])
                if any(assessment['values'][k]!=v for k,v in solver.items()):raise ValueError('Solver observations differ')
                stable=stationary_fields(out/'case',e['spec'],context)
                if stable!=read(out/'observations/field-stationarity.json'):raise ValueError('Stationarity mismatch')
                sample=[ev for ev in record.data['events'] if ev.get('operation')=='sample'][-1]
                root=out/sample.get('output_archive_root','observations/sampling')
                expected=reference_measurements(root/'profile.xy',root/'pressure.xy',e['spec'])
                if any(abs(assessment['values'][k]-v)>1e-12 for k,v in expected.items()):raise ValueError('Reference mismatch')
    if totals['model_tokens']!=p['model_tokens'] or totals['model_tokens']!=p['known_model_tokens']:raise ValueError('Batch usage mismatch')
    report(batch)
    write_json(batch/'archive-verification.json',dict(freeze_sha256=digest,offline_verification=True,new_model_calls=0,new_simulations=0,totals=totals,scoring=read(batch/'results.json')['scoring']))
    print(json.dumps(totals))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('freeze','batch','run','report','verify','check-freeze'));parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--id')
    args=parser.parse_args();batch=args.batch.resolve()
    if args.action=='run':return run(batch,args.id)
    if args.action=='check-freeze':verify_freeze(batch);print('Frozen bytes and schedule verified; no model/solver execution');return 0
    return dict(freeze=freeze,batch=coordinate,report=report,verify=verify)[args.action](batch)


if __name__=='__main__':sys.exit(main())
