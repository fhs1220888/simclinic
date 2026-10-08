"""Prospective sampling-policy comparison; held parameters have no prior runs."""
import hashlib
import json
from pathlib import Path

from .content_study import TASKS as CONTENT_TASKS
from .heldout import DEVELOPMENT, TASKS as FIELD_TASKS, verify_hashes

PRIOR_SAMPLING_TASKS = [dict(id='J1', mean_velocity=0.022, nx=56, ny=28),
                        dict(id='J2', mean_velocity=0.028, nx=56, ny=28),
                        dict(id='J3', mean_velocity=0.022, nx=64, ny=32)]
PRIOR_K_TASKS = [dict(id='K1', mean_velocity=0.024, nx=52, ny=26),
                 dict(id='K2', mean_velocity=0.030, nx=52, ny=26),
                 dict(id='K3', mean_velocity=0.024, nx=68, ny=34)]
TASKS = [dict(id='L1', mean_velocity=0.021, nx=54, ny=27),
         dict(id='L2', mean_velocity=0.029, nx=54, ny=27),
         dict(id='L3', mean_velocity=0.021, nx=66, ny=33)]
POLICIES = dict(baseline='legacy', candidate='sampling')
INFERENCE_SCOPE = ('仅比较注册采样完成策略；两组使用同一 content 源码、模型/提示词、'
                   'U 字段规则、reviewer、环境与原 CFD/任务验收。诊断准确性与任务成功分列。')


def validate_new_tasks(tasks=TASKS):
    old = FIELD_TASKS + CONTENT_TASKS + PRIOR_SAMPLING_TASKS + PRIOR_K_TASKS + [DEVELOPMENT]
    keys = ('mean_velocity', 'nx', 'ny')
    seen = set()
    for spec in tasks:
        values = tuple(spec[k] for k in keys)
        if spec['id'] in seen or any(values == tuple(p[k] for k in keys) for p in old):
            raise ValueError('Held tasks must be distinct from all developed H/I/D0 tasks')
        if spec['mean_velocity'] <= 0 or any(type(spec[k]) is not int or spec[k] <= 0 for k in ('nx', 'ny')):
            raise ValueError('Invalid held task parameters')
        seen.add(spec['id'])
    if len({tuple(p[k] for k in keys) for p in tasks}) != len(tasks):
        raise ValueError('Duplicate held parameters')


def schedule():
    return [dict(id=f"{spec['id']}-r{repeat}-{arm}", task=spec['id'], repeat=repeat, arm=arm)
            for repeat in (1, 2, 3) for index, spec in enumerate(TASKS)
            for arm in (('baseline', 'candidate') if (repeat + index) % 2 else ('candidate', 'baseline'))]


def completion_class(arm):
    from .heldout import HeldoutCompletion
    from ..domains.openfoam.sampling_gate import GatedHeldoutCompletion
    if arm not in POLICIES:
        raise ValueError('Unknown evaluation arm')
    return HeldoutCompletion if arm == 'baseline' else GatedHeldoutCompletion


def verify_sampling_freeze(batch):
    batch = Path(batch).resolve()
    frozen = json.loads((batch / 'freeze.json').read_text())
    digest = hashlib.sha256((batch / 'freeze.json').read_bytes()).hexdigest()
    if digest != (batch / 'freeze.sha256').read_text().strip():
        raise ValueError('Freeze manifest changed')
    if frozen['experiment'] != 'registered-sampling-gate-v1' or frozen['completion_policies'] != POLICIES:
        raise ValueError('Unexpected sampling policy protocol')
    if frozen['variants']['baseline'] != frozen['variants']['candidate']:
        raise ValueError('Native source must be identical between policies')
    for folder, key in (('common-runtime', 'common_runtime'), ('corpus', 'corpus')):
        verify_hashes(batch / folder, frozen[key])
    for arm, expected in frozen['variants'].items():
        verify_hashes(batch / 'variants' / arm, expected)
    for name, expected in frozen['tasks'].items():
        verify_hashes(batch / 'tasks' / name, expected)
    for name, key in (('requirements.lock', 'requirements_sha256'), ('arm-difference.patch', 'arm_difference_sha256')):
        if hashlib.sha256((batch / name).read_bytes()).hexdigest() != frozen[key]:
            raise ValueError('Frozen auxiliary file changed: ' + name)
    return frozen, digest


def development_ready(progress, frozen):
    entries = progress['development']
    if [e['id'] for e in entries] != ['D0-baseline', 'D0-candidate'] or progress['runs']:
        return False
    return all(e.get('status') == 'finished' and e.get('quality_met') is True and
               e.get('evidence_complete') is True and type(e.get('model_tokens')) is int and
               e['model_tokens'] >= 0 for e in entries) and \
        progress['consumed_model_tokens'] == sum(e['model_tokens'] for e in entries) and \
        progress['consumed_model_tokens'] <= frozen['development_model_token_limit']
