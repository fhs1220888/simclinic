"""Rebuild explicitly synthetic fixtures; never executes OpenFOAM."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'fixtures'
ENV = {'distribution': 'OpenFOAM Foundation', 'version': '10', 'solver': 'simpleFoam',
       'regime': 'steady-incompressible-single-phase', 'runtime_digest': 'synthetic-env-v1'}


def event(id, kind, **fields):
    return dict(id=id, kind=kind, run_id='run-new', input_version='sha256:input-v2', **fields)


def tool(id, phase, op, outcome='pass', exit_code=0, fingerprint='state-v2', **fields):
    return event(id, 'tool', phase=phase, operation=op, outcome=outcome, exit_code=exit_code,
                 input_fingerprint=fingerprint, error_category=fields.pop('error_category', None),
                 arguments=fields.pop('arguments', {}), **fields)


def base(name):
    return {'schema_version': '1.0', 'record_id': name, 'synthetic': True,
        'run_id': 'run-new', 'input_version': 'sha256:input-v2',
        'environment': ENV, 'agent': {'name': 'synthetic-agent', 'version': 'fixture-v1'},
        'task': {'id': 'synthetic-channel', 'objective': '合成通道测试；不代表真实 CFD 求解。',
                 'required_preflight': ['validate_case', 'checkMesh'],
                 'acceptance': [
                    {'id': 'ended', 'description': '执行结束证据', 'stage': 'execution', 'operator': 'eq', 'target': True},
                    {'id': 'residual', 'description': '合成稳态监测量阈值（不是线性求解器残差）', 'stage': 'numerical', 'operator': 'le', 'target': 1e-5},
                    {'id': 'reference_error', 'description': '合成参考量相对误差', 'stage': 'reference', 'operator': 'le', 'target': 0.02}]},
        'coverage': {k: True for k in ('tools', 'decisions', 'references', 'acceptance')},
        'metrics': {k: None for k in ('wall_seconds', 'solver_seconds', 'model_tokens', 'model_cost')},
        'artifacts': [{'id': 'results', 'path': 'results.json', 'run_id': 'run-new', 'input_version': 'sha256:input-v2'}],
        'events': [tool('validate', 'preflight', 'validate_case'),
                   event('handled-validate', 'decision', handles=['validate']),
                   tool('mesh', 'preflight', 'checkMesh'),
                   event('handled-mesh', 'decision', handles=['mesh']),
                   tool('solve', 'solve', 'simpleFoam'),
                   event('result-ref', 'reference', artifact_id='results'),
                   event('m-ended', 'measurement', criterion_id='ended', value=True, artifact_id='results'),
                   event('m-residual', 'measurement', criterion_id='residual', value=2e-6, artifact_id='results'),
                   event('m-reference', 'measurement', criterion_id='reference_error', value=0.01, artifact_id='results'),
                   event('complete', 'claim', claim_type='completion', success=True)]}


def write(name, record, result_values=None):
    directory = ROOT / name
    (directory / 'case').mkdir(parents=True, exist_ok=True)
    values = result_values or {'synthetic': True, 'ended': True, 'residual': 2e-6, 'reference_error': 0.01}
    raw = (json.dumps(values, ensure_ascii=False, indent=2) + '\n').encode()
    (directory / 'case' / 'results.json').write_bytes(raw)
    for artifact in record['artifacts']:
        artifact['sha256'] = hashlib.sha256(raw).hexdigest()
    (directory / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    write('healthy', base('synthetic-healthy'))
    fault = base('synthetic-faults')
    fault['events'] = [e for e in fault['events'] if e['id'] not in ('validate', 'handled-validate')]
    fault['events'][0].update(outcome='fail', exit_code=1, error_category='mesh_invalid')
    solve = next(e for e in fault['events'] if e['id'] == 'solve')
    solve.update(outcome='fail', exit_code=1, error_category='foam_fatal_io')
    retry = copy.deepcopy(solve)
    retry.update(id='retry', retry_of='solve')
    fault['events'].insert(3, retry)
    fault['artifacts'][0].update(run_id='run-old', input_version='sha256:input-v1')
    next(e for e in fault['events'] if e['id'] == 'm-residual')['value'] = 0.1
    write('faults', fault, {'synthetic': True, 'ended': False, 'residual': 0.1, 'reference_error': 0.2})
    missing = base('synthetic-missing-evidence')
    missing['coverage'] = {k: False for k in missing['coverage']}
    missing['events'] = [tool('solve', 'solve', 'simpleFoam', outcome='unknown', exit_code=None, fingerprint=None),
                         event('result-ref', 'reference', artifact_id='results'),
                         event('complete', 'claim', claim_type='completion', success=True)]
    missing['artifacts'][0].update(run_id=None, input_version=None)
    write('missing', missing)
    stale = base('synthetic-stale-results')
    stale['artifacts'][0].update(run_id='run-old', input_version='sha256:input-v1')
    write('stale', stale)
    recovered = base('synthetic-recovered')
    first = next(e for e in recovered['events'] if e['id'] == 'solve')
    first.update(outcome='fail', exit_code=1, error_category='tool_wrapper_error')
    repaired = tool('retry', 'solve', 'simpleFoam', fingerprint='state-v3', retry_of='solve', arguments={'wrapper': 'fixed'})
    recovered['events'].insert(5, repaired)
    write('recovered', recovered)
    no_work = base('synthetic-not-applicable')
    no_work['events'], no_work['artifacts'] = [], []
    write('not-applicable', no_work)
    text_case = ROOT / 'foam-agent-text'
    text_case.mkdir(parents=True, exist_ok=True)
    (text_case / 'workflow.log').write_text('SYNTHETIC logger-style example, not a real Foam-Agent run.\n<error_logs>\nFOAM FATAL IO ERROR\n</error_logs>\n<rewrite_plan>\nReview input before retry.\n</rewrite_plan>\n', encoding='utf-8')
    (text_case / 'review.log').write_text('<error_logs>\nFOAM FATAL IO ERROR\n</error_logs>\n', encoding='utf-8')
    metadata = base('synthetic-foam-agent-import')
    metadata.update(events=[], artifacts=[], run_id=None, input_version=None)
    metadata['agent'] = {'name': 'Foam-Agent', 'version': None, 'note': 'synthetic logger-shape only'}
    (text_case / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log = 'SYNTHETIC Foundation 10 simpleFoam-style excerpt; not generated by a solver.\nTime = 10\nsmoothSolver: Solving for Ux, Initial residual = 0.01, Final residual = 1e-07, No Iterations 2\nGAMG: Solving for p, Initial residual = 0.02, Final residual = 1e-06, No Iterations 3\nEnd\n'
    (ROOT / 'healthy' / 'case' / 'log.simpleFoam').write_text(log, encoding='utf-8')


if __name__ == '__main__':
    main()
