"""Offline verification of sampling gate development runs; no new execution."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.record import load_record
from simclinic.core.report import write_json
from simclinic.domains.openfoam.logs import parse_solver_log
from simclinic.evaluation.heldout import hashes, pressure_error, velocity_error, verify_hashes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(batch):
    protocol = json.loads((batch / 'protocol.json').read_text())
    digest = hashlib.sha256((batch / 'protocol.json').read_bytes()).hexdigest()
    require(digest == (batch / 'protocol.sha256').read_text().strip(), 'Protocol changed')
    for folder, key in (('common-runtime', 'common_runtime'), ('native', 'native'), ('task', 'task')):
        verify_hashes(batch / folder, protocol[key])
    for name in ('simclinic/core/record.py', 'simclinic/domains/openfoam/acceptance.py',
                 'simclinic/domains/openfoam/completion.py', 'simclinic/domains/openfoam/logs.py',
                 'simclinic/evaluation/heldout.py'):
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == protocol['common_runtime'][name],
                'Scientific/record helper differs from the run snapshot: ' + name)
    old_format = "        super().__init__(code + ': ' + message)\n"
    new_format = "        prefix = code + ': '\n        super().__init__(message if message.startswith(prefix) else prefix + message)\n"
    delivered = (ROOT / 'simclinic/domains/openfoam/sampling_gate.py').read_text()
    frozen_gate = (batch / 'common-runtime/simclinic/domains/openfoam/sampling_gate.py').read_text()
    require(delivered == frozen_gate or delivered.replace(new_format, old_format) == frozen_gate,
            'Delivered gate differs beyond the documented error-prefix formatting fix')
    rows, versions, run_ids = [], {}, set()
    total_artifacts = 0
    spec = json.loads((batch / 'task/spec.json').read_text())
    for entry in protocol['schedule']:
        out = batch / 'runs' / entry['id']
        record = load_record(out / 'record.json', out).data
        summary = json.loads((out / 'summary.json').read_text())
        environment = json.loads((out / 'environment.json').read_text())
        require(environment['protocol_sha256'] == digest, 'Wrong run protocol')
        require(record['synthetic'] is False and record['heldout'] is False and record['development_regression'] is True,
                'Development provenance missing')
        require(not record['model_calls'] and record['metrics']['model_tokens'] == 0
                and not (out / 'model-calls').exists(), 'Unexpected model activity')
        verify_hashes(out / 'source-package', protocol['native'])
        source = protocol['sources'][entry['scenario']]
        verify_hashes(batch / 'inputs' / entry['scenario'], source['input_hashes'])
        require(record['fault_injection'] == source.get('fault_injection', False), 'Injection label differs')
        require(len(record['attempts']) == 1, 'Unexpected extra execution')
        attempt = record['attempts'][0]
        require(attempt['run_id'] != source['source_run_id'] and attempt['run_id'] not in run_ids,
                'Historical or duplicate run identity reused')
        run_ids.add(attempt['run_id'])
        require(attempt['execution']['exit_code'] == 0 and attempt['execution']['policy_validated'], 'Solver failed unexpectedly')
        verify_hashes(out / attempt['input_snapshot'], source['input_hashes'])
        versions.setdefault(entry['scenario'], set()).add(attempt['input_version'])
        native_manifest = json.loads((out / 'attempts' / attempt['run_id'] / 'native/input-manifest.json').read_text())
        calculated = 'sha256:' + hashlib.sha256(json.dumps(native_manifest['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        require(calculated == attempt['input_version'], 'Input version mismatch')
        for artifact in record['artifacts']:
            path = out / artifact['path']
            require(hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256'], 'Artifact changed: ' + artifact['path'])
        total_artifacts += len(record['artifacts'])
        expected_success = entry['scenario'] == 'healthy'
        require(summary['completion']['success'] is expected_success, 'Unexpected completion')
        assessment = json.loads((out / 'observations/task-assessment.json').read_text())
        require(assessment['criteria'] == record['task']['acceptance'], 'Task criteria changed')
        require(summary['acceptance']['rendered_velocity'] is True, 'U evidence missing')
        sample = next(e for e in record['events'] if e.get('operation') == 'sample')
        require(sample['exit_code'] == 0, 'Unexpected sample exit status')
        logs = [parse_solver_log((out / 'observations/sampling' / name).read_text(), name)
                for name in ('stdout.log', 'stderr.log')]
        fatal = [item for log in logs for item in log['fatal_errors']]
        require(bool(fatal) == (entry['scenario'] == 'archived-fatal'), 'Fatal log expectation differs')
        if expected_success:
            require(abs(velocity_error(out / 'observations/sampling/profile.xy', spec) - summary['acceptance']['profile_error']) < 1e-12,
                    'Velocity error recomputation differs')
            require(abs(pressure_error(out / 'observations/sampling/pressure.xy', spec) - summary['acceptance']['pressure_gradient_error']) < 1e-12,
                    'Pressure error recomputation differs')
            require(all(record['coverage'].values()), 'Positive evidence incomplete')
        else:
            require(summary['acceptance']['profile_error'] is None and summary['acceptance']['pressure_gradient_error'] is None,
                    'Missing reference was fabricated')
            require(record['coverage']['references'] is False, 'Missing reference coverage upgraded')
        if entry['policy'] == 'sampling':
            gate = json.loads((out / 'observations/sampling/gate-assessment.json').read_text())
            require(gate['outcome'] == sample['outcome'] and gate['exit_code'] == sample['exit_code'], 'Gate/tool disagree')
            decision = next(e for e in record['events'] if e.get('decision_type') == 'sampling_gate')
            require(decision['handles'] == [sample['id']] and decision['action'] == ('continue' if expected_success else 'stop'),
                    'Gate decision missing or inconsistent')
            expected_category = None if expected_success else 'sampling_fatal_log' if fatal else 'sampling_missing_output'
            require(sample['error_category'] == expected_category and sample['outcome'] == ('pass' if expected_success else 'fail'),
                    'Typed outcome differs from actual evidence')
        else:
            require(sample['outcome'] == 'pass', 'Legacy sample outcome changed')
            if not expected_success:
                require(summary['acceptance_error']['type'] == 'StopIteration', 'Legacy diagnostic changed')
        rows.append(dict(id=entry['id'], scenario=entry['scenario'], policy=entry['policy'],
                         fault_injection=record['fault_injection'], quality_met=expected_success,
                         sample_exit=sample['exit_code'], sample_outcome=sample['outcome'],
                         error=summary['acceptance_error'], fatal_log_lines=[e['line'] for e in fatal],
                         input_version=attempt['input_version'], run_id=attempt['run_id'],
                         artifacts_verified=len(record['artifacts']), reference_recomputed=expected_success))
    require(all(len(value) == 1 for value in versions.values()), 'Arms used different inputs')
    result = dict(offline_verification=True, new_simulations=0, new_model_calls=0,
                  development_regression=True, heldout=False, protocol_sha256=digest,
                  frozen_sources_verified=True, paired_input_hashes_equal=True, fresh_solver_run_ids=True,
                  artifact_count=total_artifacts, run_count=len(rows), runs=rows,
                  delivered_gate_matches_snapshot=delivered == frozen_gate,
                  delivery_delta=None if delivered == frozen_gate else 'Error message prefix formatting only; initial records retained.',
                  verification_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  optimization_effect_proven=False)
    write_json(batch / 'verification.json', result)
    lines = ['# 采样门禁开发回归', '',
             '使用冻结的历史输入重新求解、生成 U 图并采样；没有复用历史求解输出，没有模型调用。入口为原生 local_runner，没有验证新的模型规划路径。这是开发回归，不计入保留评估或自然故障率。', '',
             '| 运行 | 明确注入 | 采样退出 | 工具判定 | 完整任务 | 错误 |',
             '| --- | --- | ---: | --- | --- | --- |']
    for row in rows:
        error = row['error'] or {}
        label = error.get('code', error.get('type', '—'))
        lines.append(f"| {row['id']} | {row['fault_injection']} | {row['sample_exit']} | {row['sample_outcome']} | {row['quality_met']} | {label} |")
    lines += ['', f"[离线核验](verification.json)通过 {total_artifacts} 个注册证据文件，重新计算正常样本的速度/压力参考误差；失败样本缺失参考数据，误差仍为 null、references 覆盖仍为 false。配对输入哈希一致，实际运行 ID 全部不同于原归档。", '',
              '原 CFD 条件和参考阈值没有放宽。新门禁不自动修复文件或调用模型；旧策略本来就没有误报完整任务成功。本次改进是正确标注采样失败、提供原因与停止证据，并保护测量发布顺序，不证明成功率、耗时或模型消耗改善。', '']
    if result['delivery_delta']:
        lines += ['该协议冻结后发现错误消息重复前缀。交付代码仅修正这个显示问题；原始记录保留，另有完整新协议复测。冻结门禁与交付门禁除了此前缀格式外字节一致。', '']
    (batch / 'report.md').write_text('\n'.join(lines))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, nargs='+', required=True)
    args = parser.parse_args()
    results = [verify(batch.resolve()) for batch in args.batch]
    print(json.dumps(dict(batches=len(results), runs=sum(r['run_count'] for r in results),
                         artifacts=sum(r['artifact_count'] for r in results), new_model_calls=0,
                         new_simulations=0, verification=True), indent=2))


if __name__ == '__main__':
    main()
