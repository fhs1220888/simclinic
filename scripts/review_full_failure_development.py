"""Offline evidence/ordering review and explicitly unverified design candidates."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.result_guard import read_verified_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    archive = json.loads((batch / 'archive-verification.json').read_text())
    digest = hashlib.sha256((batch / 'freeze.json').read_bytes()).hexdigest()
    frozen = json.loads((batch / 'freeze.json').read_text())
    if archive['freeze_sha256'] != digest or digest != (batch / 'freeze.sha256').read_text().strip():
        raise ValueError('Verified freeze required')
    rows, candidates = [], []
    lines = ['# 失败路径证据与候选改进', '',
             '已确认事实引用注册证据；注入原因有直接干预记录。自然根因、恢复收益和总体优化仍未验证。', '']
    for scenario in frozen['schedule']:
        out = batch / 'runs' / scenario
        record = load_record(out / 'record.json', out)
        s = json.loads((out / 'summary.json').read_text())
        events = record.data['events']
        claims = [(i, e) for i, e in enumerate(events) if e['kind'] == 'claim' and e.get('claim_type') == 'completion']
        if len(claims) != 1:
            raise ValueError('Expected one explicit completion')
        claim_index, claim = claims[0]
        claim_artifact = record.artifact(claim['source_artifact'])
        payload = json.loads(read_verified_result(record, claim_artifact['id'], claim['run_id'], claim['input_version']))
        if payload['payload']['completion_claim'] != s['completion'] or claim['success'] != s['completion']['success']:
            raise ValueError('Completion does not match registered graph source')
        measures = [(i, e) for i, e in enumerate(events) if e['kind'] == 'measurement']
        if len(measures) != len(record.data['task']['acceptance']) or any(i >= claim_index for i, e in measures):
            raise ValueError('Measurements must precede completion')
        for i, e in measures:
            if e['value'] != s['acceptance'][e['criterion_id']] or (e['run_id'], e['input_version']) != (claim['run_id'], claim['input_version']):
                raise ValueError('Measurement identity/value mismatch')
        sample = s['sample_tool']
        gate_artifact = record.artifact(sample['source_artifact'])
        gate = json.loads(read_verified_result(record, gate_artifact['id'], claim['run_id'], claim['input_version']))
        refs = [dict(event_id=claim['id'], artifact_id=claim_artifact['id'], path=claim_artifact['path'], sha256=claim_artifact['sha256']),
                dict(event_id=sample['id'], artifact_id=gate_artifact['id'], path=gate_artifact['path'], sha256=gate_artifact['sha256'])]
        for fatal in gate['fatal_errors']:
            a = record.artifact(fatal['artifact_id'])
            log = read_verified_result(record, a['id'], claim['run_id'], claim['input_version']).decode().splitlines()
            # The primary parser normalizes surrounding whitespace in excerpts.
            if log[fatal['line'] - 1].strip() != fatal['excerpt']:
                raise ValueError('Fatal line pointer differs')
            refs.append(dict(artifact_id=a['id'], path=a['path'], sha256=a['sha256'], line=fatal['line']))
        if scenario != 'healthy':
            injection_events = [e for e in events if e.get('observation_type') == 'explicit_development_injection']
            if len(injection_events) != 1 or events.index(injection_events[0]) >= events.index(sample):
                raise ValueError('Injection must be registered before sampler result')
            a = record.artifact(injection_events[0]['source_artifact'])
            injection = json.loads(read_verified_result(record, a['id'], claim['run_id'], claim['input_version']))
            refs.append(dict(event_id=injection_events[0]['id'], artifact_id=a['id'], path=a['path'], sha256=a['sha256']))
            payload_artifact = record.artifact(injection['payload_artifact'])
            raw = read_verified_result(record, payload_artifact['id'], claim['run_id'], claim['input_version'])
            refs.append(dict(artifact_id=payload_artifact['id'], path=payload_artifact['path'], sha256=payload_artifact['sha256']))
            if scenario == 'missing-profile':
                if hashlib.sha256(raw).hexdigest() != injection['removed_sha256']:
                    raise ValueError('Removed bytes differ from injection evidence')
                if (out / 'observations/sampling/profile.xy').exists():
                    raise ValueError('Absent sample was published')
            candidate = dict(id='configuration-contract' if scenario == 'fatal-sample' else 'bounded-resampling',
                scenario=scenario, status='not_applied_benefit_unverified', evidence=refs,
                confirmed_cause='开发注入器明确构造了错误的独立采样字典' if scenario == 'fatal-sample' else '开发注入器明确删除了新生成的 profile 文件',
                cause_hypothesis='其他后处理配置入口可能绕过字典验证；本次注入没有证明这种自然缺陷存在' if scenario == 'fatal-sample' else '自然输出丢失可能有其他原因，本次注入无法确认',
                change='将严格的正文和字典契约扩展到其他采样配置入口' if scenario == 'fatal-sample' else '评审一次有明确预算的重采样：先重新核验身份，耗尽或持续失败时继续诚实停止',
                expected_effect='保留合法输入，提前返回可定位的格式错误' if scenario == 'fatal-sample' else '恢复短暂输出丢失，同时禁止发布部分或旧参考测量',
                retest=['合法字典保持原样', 'JSON 封装', '重复键', '身份冲突'] if scenario == 'fatal-sample' else ['一次短暂丢失', '持续丢失', '旧输入或运行身份', '预算耗尽'],
                application_requires_new_protocol=True)
            candidates.append(candidate)
        row = dict(scenario=scenario, confirmed_sampler_outcome=gate['outcome'], actual_exit_code=gate['exit_code'],
                   error_category=gate['error_category'], completion_success=claim['success'],
                   measurement_before_completion_verified=True, evidence=refs,
                   agent_behavior_findings=s['audit_summary']['finding'], fault_injection=s['fault_injection'])
        rows.append(row)
        lines += [f'## {scenario}', '', f"采样退出 `{gate['exit_code']}`，门禁 `{gate['outcome']}`，类别 `{gate['error_category']}`，完成声明 `{claim['success']}`。测量先于声明且身份一致；五类 Agent 检查发现问题 {s['audit_summary']['finding']} 项。", '']
        for ref in refs:
            label = ref['path'] + (':' + str(ref['line']) if 'line' in ref else '')
            lines.append(f"- [{label}](runs/{scenario}/{ref['path']})；证据 `{ref['artifact_id']}`。")
        lines.append('')
    lines += ['## 候选改进', '', '以下候选未应用、收益未验证；两条故障链路当前已诚实失败，不应伪造 Agent 行为缺陷。', '']
    for candidate in candidates:
        lines += [f"- `{candidate['id']}`：{candidate['change']}",
                  f"  - 已确认原因：{candidate['confirmed_cause']}",
                  f"  - 原因假设：{candidate['cause_hypothesis']}",
                  f"  - 预期作用：{candidate['expected_effect']}",
                  '  - 复测：' + '；'.join(candidate['retest'])]
    result = dict(freeze_sha256=digest, analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  offline_review=True, new_model_calls=0, new_simulations=0, rows=rows, candidates=candidates,
                  hidden_reasoning_collected=False, optimization_effect_proven=False)
    write_json(batch / 'evidence-review.json', result)
    (batch / 'evidence-review.md').write_text('\n'.join(lines) + '\n')
    print('Evidence and completion ordering verified:', len(rows), 'paths;', len(candidates), 'unapplied candidates')


if __name__ == '__main__':
    main()
