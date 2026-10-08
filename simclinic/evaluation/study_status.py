"""Read frozen-study progress without executing, resuming or editing the study."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .heldout import verify_hashes


def _count(entries, planned):
    finished = [e for e in entries if e['status'] != 'running']
    return dict(planned=planned,finished=len(finished),active=sum(e['status']=='running' for e in entries),
        not_started=planned-len(entries),quality_passed=sum(e.get('quality_met') is True for e in finished),
        quality_failed=sum(e.get('quality_met') is False for e in finished),
        quality_unknown=sum(e.get('quality_met') is None for e in finished),
        evidence_incomplete=sum(e.get('evidence_complete') is not True for e in finished))


def study_status(directory):
    batch = Path(directory).resolve()
    frozen = json.loads((batch/'freeze.json').read_text())
    digest = hashlib.sha256((batch/'freeze.json').read_bytes()).hexdigest()
    if digest != (batch/'freeze.sha256').read_text().strip():
        raise ValueError('Freeze manifest changed')
    for folder,key in (('common-runtime','common_runtime'),('corpus','corpus')):
        verify_hashes(batch/folder,frozen[key])
    for arm, expected in frozen['variants'].items():verify_hashes(batch/'variants'/arm,expected)
    for task, expected in frozen['tasks'].items():verify_hashes(batch/'tasks'/task,expected)
    for name,key in (('requirements.lock','requirements_sha256'),('arm-difference.patch','arm_difference_sha256')):
        if key in frozen and hashlib.sha256((batch/name).read_bytes()).hexdigest()!=frozen[key]:
            raise ValueError('Frozen auxiliary file changed: '+name)
    progress_file = batch/'progress.json'
    progress = json.loads(progress_file.read_text()) if progress_file.exists() else dict(
        status='frozen_not_started',development=[],runs=[],consumed_model_tokens=0,freeze_sha256=digest)
    if progress['freeze_sha256'] != digest:raise ValueError('Progress belongs to another freeze')
    scheduled = frozen['schedule']
    held = progress['runs'];development = progress['development']
    if len(held)>len(scheduled):raise ValueError('More runs than planned')
    for expected,actual in zip(scheduled,held):
        if any(actual.get(key)!=expected.get(key) for key in ('id','task','repeat','arm')):
            raise ValueError('Progress differs from the frozen schedule')
    development_ids = ['D0-baseline','D0-candidate']
    if [e['id'] for e in development] != development_ids[:len(development)] or len(development)>2:
        raise ValueError('Unexpected development entries')
    active = [];known_active = 0;unknown_active = 0;known_finished_partial=0;unknown_finished_calls=0
    finished = [e for e in development+held if e['status']!='running']
    tokens = [e.get('model_tokens') for e in finished]
    if any(v is not None and (type(v) is not int or v<0) for v in tokens):
        raise ValueError('Invalid usage value')
    completed_known = sum(v for v in tokens if v is not None)
    if progress['consumed_model_tokens'] != completed_known:
        raise ValueError('Completed usage disagrees with progress accumulator')
    for group,entries in (('development',development),('runs',held)):
        for entry in entries:
            expected = (batch/group/entry['id']).resolve()
            if not expected.is_relative_to(batch) or Path(entry['directory']).resolve()!=expected:
                raise ValueError('Run directory must match this batch')
            running=entry['status']=='running'
            if not running and entry.get('model_tokens') is not None:continue
            metadata = sorted((expected/'model-calls').glob('*/metadata.json'))
            known = 0;unknown = 0;last_call = None
            for path in metadata:
                if not path.resolve().is_relative_to(batch):raise ValueError('Metadata escaped the batch')
                last_call = path.parent.name
                try:
                    call = json.loads(path.read_text())
                except (OSError,ValueError):
                    unknown+=1;continue # A live writer may be updating this file.
                value = call.get('total_tokens')
                if type(value) is int and value>=0:known+=value
                else:unknown+=1
            if not running:
                known_finished_partial+=known;unknown_finished_calls+=unknown
                continue
            known_active+=known;unknown_active+=unknown
            active.append(dict(id=entry['id'],arm=entry['arm'],task=entry['task'],group=group,
                model_call_records=len(metadata),last_model_call=last_call,
                known_model_tokens=known,model_calls_with_unknown_usage=unknown))
    unknown_finished = sum(v is None for v in tokens)
    lower_bound = completed_known+known_finished_partial+known_active
    total = lower_bound if unknown_finished==0 and unknown_active==0 else None
    limit = frozen.get('batch_model_token_limit')
    return dict(snapshot_at=datetime.now(timezone.utc).isoformat(),batch=str(batch),
        experiment=frozen['experiment'],status=progress['status'],freeze_sha256=digest,
        frozen_files_verified=True,read_only=True,new_model_calls=0,new_simulations=0,
        held=_count(held,len(scheduled)),development=_count(development,2),
        arms={arm:_count([e for e in held if e['arm']==arm],sum(e['arm']==arm for e in scheduled))
              for arm in ('baseline','candidate')},active_runs=active,
        usage=dict(completed_model_tokens=completed_known,finished_runs_with_unknown_usage=unknown_finished,
            finished_partial_known_model_tokens=known_finished_partial,finished_model_calls_with_unknown_usage=unknown_finished_calls,
            active_known_model_tokens=known_active,active_model_calls_with_unknown_usage=unknown_active,
            observed_model_tokens_lower_bound=lower_bound,observed_model_tokens=total,
            batch_model_token_limit=limit,remaining_from_observed_usage=limit-total if limit is not None and total is not None else None,
            accounting='Includes development, partial finished and active known usage; pending/unknown usage is not zero. Limit is checked between runs.'),
        inference_scope=frozen.get('inference_scope','See frozen experiment definition'),
        optimization_effect_proven=False,
        interpretation='Progress and coordinator outcomes only; no new CFD validation or optimization inference.')


def status_text(status):
    h,d = status['held'],status['development']
    lines = [f"状态：{status['status']}；保留运行 {h['finished']}/{h['planned']}，正在运行 {h['active']}；开发检查 {d['finished']}/{d['planned']}。",
        f"已结束保留运行：任务通过 {h['quality_passed']}，任务失败 {h['quality_failed']}，质量未知 {h['quality_unknown']}，证据不完整 {h['evidence_incomplete']}。"]
    for active in status['active_runs']:
        lines.append(f"当前：{active['id']}；已创建模型调用记录 {active['model_call_records']}，最近 {active['last_model_call']}。")
    usage = status['usage']
    lines.append(f"已结束运行用量：{usage['completed_model_tokens']} token；含当前调用的已知下限：{usage['observed_model_tokens_lower_bound']} token。")
    if usage.get('finished_partial_known_model_tokens'):
        lines.append(f"总量未知的已结束运行，已确认部分为 {usage['finished_partial_known_model_tokens']} token；其余未知不填零。")
    if usage['observed_model_tokens'] is None:lines.append('存在未知或进行中的调用用量，总用量及预算余量保持未知。')
    lines.append('冻结文件核验通过；仅查询进度，没有新增模型或仿真调用。')
    return '\n'.join(lines)
