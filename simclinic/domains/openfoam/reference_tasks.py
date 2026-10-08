"""Two explicitly authored analytic reference mechanisms, Foundation 10 only.

Development templates are not independent heldout evidence. Pressure metric
retains the integration key but is normalized by a nonzero declared scale, not
by the zero reference gradient. Both sampled lines include all U components.
"""
import math
import hashlib
import json
from pathlib import Path
import re
import shutil

from .acceptance import projected_rows, solver_measurements
from .channel import dictionary
from .completion import task_definition, canonical_dictionary
from .sampling_gate import GatedChannelCompletion
from ...core.report import write_json
from ...checks.behavior import matches
from ...foam_runtime import current_record, verified_workspace
from ...result_guard import ResultRejected, read_verified_result


DEVELOPMENT_TASKS = [
    dict(id='C0', kind='couette', wall_speed=0.02, nx=40, ny=20),
    dict(id='F0', kind='oblique_uniform', velocity=[0.012, 0.007, 0.0], nx=40, ny=20),
]


def validate_spec(spec):
    if spec.get('kind') not in ('couette', 'oblique_uniform'):
        raise ValueError('Unsupported reference mechanism')
    for key in ('nx', 'ny'):
        if type(spec.get(key)) is not int or not 4 <= spec[key] <= 200:
            raise ValueError('Unsupported mesh count: ' + key)
    if spec['kind'] == 'couette':
        speed = spec.get('wall_speed')
        if type(speed) not in (int, float) or not math.isfinite(speed) or not 0 < speed <= 0.05:
            raise ValueError('Invalid wall speed')
    else:
        v = spec.get('velocity')
        if not isinstance(v, list) or len(v) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in v):
            raise ValueError('Invalid uniform velocity')
        if not 0 < v[0] <= 0.05 or not 0 < v[1] <= 0.05 or v[2] != 0:
            raise ValueError('This topology requires positive x/y velocity and zero z')
    return spec


def analytic_velocity(spec, x, y):
    validate_spec(spec)
    return (spec['wall_speed'] * y / 0.1, 0.0, 0.0) if spec['kind'] == 'couette' else tuple(spec['velocity'])


def pressure_gradient_scale(spec):
    validate_spec(spec)
    speed = spec['wall_speed'] if spec['kind'] == 'couette' else math.sqrt(sum(v*v for v in spec['velocity']))
    return 0.001 * speed / 0.1**2


def create_reference(template, destination, spec):
    validate_spec(spec)
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Reference destination must be fresh')
    shutil.copytree(template, destination)
    # New-task v2 output cadence supplies two independently archived states.
    # Solver/residual controls and physical parameters are unchanged.
    control = destination / 'system/controlDict'
    text, count = re.subn(r'\bwriteInterval\s+2000\s*;', 'writeInterval 100;', control.read_text())
    if count != 1:
        raise ValueError('Expected unchanged development output cadence')
    control.write_text(text)
    kind = spec['kind']
    top_type = bottom_type = 'wall' if kind == 'couette' else 'patch'
    body = f'''convertToMeters 1;
vertices ((0 0 0) (1 0 0) (1 0.1 0) (0 0.1 0) (0 0 0.01) (1 0 0.01) (1 0.1 0.01) (0 0.1 0.01));
blocks (hex (0 1 2 3 4 5 6 7) ({spec['nx']} {spec['ny']} 1) simpleGrading (1 1 1));
edges ();
boundary (
 west {{type patch; faces ((0 4 7 3));}}
 east {{type patch; faces ((1 2 6 5));}}
 south {{type {bottom_type}; faces ((0 1 5 4));}}
 north {{type {top_type}; faces ((3 7 6 2));}}
 frontAndBack {{type empty; faces ((0 3 2 1) (4 5 6 7));}}
);
mergePatchPairs ();
'''
    (destination / 'system/blockMeshDict').write_text(dictionary('blockMeshDict', body))
    if kind == 'couette':
        values = ' '.join(f"({spec['wall_speed']*(i+0.5)/spec['ny']:.12g} 0 0)" for i in range(spec['ny']))
        velocity_boundary = f'''west {{type fixedValue; value nonuniform List<vector> {spec['ny']} ({values});}}
east {{type zeroGradient;}}
south {{type fixedValue; value uniform (0 0 0);}}
north {{type fixedValue; value uniform ({spec['wall_speed']} 0 0);}}'''
        pressure_boundary = 'west {type zeroGradient;} east {type fixedValue; value uniform 0;} south {type zeroGradient;} north {type zeroGradient;}'
    else:
        vector = ' '.join(str(v) for v in spec['velocity'])
        velocity_boundary = f'''west {{type fixedValue; value uniform ({vector});}}
south {{type fixedValue; value uniform ({vector});}}
east {{type zeroGradient;}}
north {{type zeroGradient;}}'''
        pressure_boundary = 'west {type zeroGradient;} south {type zeroGradient;} east {type fixedValue; value uniform 0;} north {type fixedValue; value uniform 0;}'
    (destination / '0/U').write_text(dictionary('U', 'dimensions [0 1 -1 0 0 0 0]; internalField uniform (0 0 0);\nboundaryField {\n' + velocity_boundary + '\nfrontAndBack {type empty;}\n}', 'volVectorField'))
    (destination / '0/p').write_text(dictionary('p', 'dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;\nboundaryField {\n' + pressure_boundary + '\nfrontAndBack {type empty;}\n}', 'volScalarField'))
    return destination


def task(spec):
    validate_spec(spec)
    if spec['kind'] == 'couette':
        physical = f"Plane Couette flow: north wall tangential speed {spec['wall_speed']} m/s, south wall stationary; west inlet Ux={spec['wall_speed']}*y/0.1, Uy=Uz=0; east velocity zeroGradient and kinematic pressure fixedValue 0. Other pressure boundaries zeroGradient."
    else:
        physical = f"Oblique uniform flow U={spec['velocity']} m/s: west/south prescribed inflow, east/north velocity zeroGradient and pressure fixedValue 0; west/south pressure zeroGradient. No solid walls in the xy plane."
    objective = f'''Generate and run an OpenFOAM Foundation 10 simpleFoam case locally.
Steady incompressible single-phase laminar flow; length 1 m, height 0.1 m,
thickness 0.01 m, uniform mesh {spec['nx']} x {spec['ny']} x 1, empty frontAndBack.
nu=0.001 m^2/s. {physical}
Use initial internal U=(0 0 0) and p=0. Generate the nine declared reference
files with west/east/south/north patches. Preserve all numerical dictionaries
and sampling points in the matching development template (writeInterval 100,
solver/residual controls unchanged); adapt only the
explicit wall speed/inlet profile or uniform vector and mesh counts. The old
Poiseuille reference is not the task. Allrun must have exactly:
set -eu
blockMesh > log.blockMesh 2>&1
checkMesh > log.checkMesh 2>&1
simpleFoam > log.simpleFoam 2>&1
No extra executable commands or controlDict function objects. Render U as PNG.
Acceptance: execution exit zero and End; SIMPLE residualControl p/U 1e-7;
steady evidence is either the SIMPLE convergence marker, or two archived
positive-time states whose max U change/declared velocity scale and max p
change/declared kinematic pressure scale are both <=1e-7. Report the original
SIMPLE marker separately, including false. Do not treat End alone as steady.
local continuity <=1e-7; all-component velocity relative L2 over both fixed
sampling lines <=0.02; norm of x/y kinematic pressure slopes divided by the
fixed scale {pressure_gradient_scale(spec):.12g} m/s^2 <=0.03. Reference pressure
gradient is zero: never divide by zero or substitute the Poiseuille gradient.
No threshold or physical-parameter changes to force acceptance.
'''
    result = task_definition(objective)
    result['id'] = 'analytic-' + spec['id']
    for criterion in result['acceptance']:
        if criterion['id'] == 'steady_converged':
            criterion.update(id='steady_evidence', description='SIMPLE 标记或两份正时间全场快照的 U/p 归一化变化均 <=1e-7；原标记单列')
        if criterion['id'] == 'task_inputs_verified':
            criterion['description'] = '生成输入符合当前解析参考任务'
        if criterion['id'] == 'profile_error':
            criterion['description'] = '两条固定采样线全部速度分量相对 L2 误差'
        if criterion['id'] == 'pressure_gradient_error':
            criterion['description'] = '零参考压强梯度的两方向斜率范数/预声明非零特征尺度；不是除以零参考值'
    result['reference_contract'] = dict(kind=spec['kind'], pressure_gradient=[0, 0],
        pressure_metric='norm_of_xy_slopes_over_declared_nonzero_scale',
        pressure_scale=pressure_gradient_scale(spec), zero_reference_division=False,
        velocity_metric='all_components_relative_l2_over_both_lines',
        steady_metric='SIMPLE_marker_or_two_verified_positive_time_full_fields',
        field_change_tolerance=1e-7, output_write_interval=100,
        velocity_scale=pressure_gradient_scale(spec)*.1**2/.001,
        kinematic_pressure_scale=pressure_gradient_scale(spec)*1.0)
    return result


def _line(path, axis, fields, count, start, step):
    data = projected_rows(path, axis, fields, len(fields)+1)
    if len(data) != count or any(abs(row[0] - (start+i*step)) > 1e-6 for i, row in enumerate(data)):
        raise ValueError('Sample coordinates differ from declared reference line')
    return data


def reference_measurements(profile, pressure, spec):
    validate_spec(spec)
    vertical = _line(profile, 'y', ('U_x', 'U_y', 'U_z'), 20, .0025, .005)
    horizontal = _line(pressure, 'x', ('U_x', 'U_y', 'U_z'), 17, .1, .05)
    rows = [(row[1:], analytic_velocity(spec, .5, row[0])) for row in vertical]
    rows += [(row[1:], analytic_velocity(spec, row[0], .05)) for row in horizontal]
    error = sum(sum((actual[i]-expected[i])**2 for i in range(3)) for actual, expected in rows)
    norm = sum(sum(v*v for v in expected) for actual, expected in rows)
    slopes = []
    for path, axis, count, start, step in ((profile, 'y', 20, .0025, .005), (pressure, 'x', 17, .1, .05)):
        samples = _line(path, axis, ('p',), count, start, step)
        mean_x = sum(r[0] for r in samples)/count
        mean_p = sum(r[1] for r in samples)/count
        slopes.append(sum((r[0]-mean_x)*(r[1]-mean_p) for r in samples)/sum((r[0]-mean_x)**2 for r in samples))
    return dict(profile_error=math.sqrt(error/norm),
                pressure_gradient_error=math.sqrt(sum(s*s for s in slopes))/pressure_gradient_scale(spec))


def couette_inlet_order(case, spec):
    """Check the real generated inlet face centres; asymmetric profiles need this."""
    def clean(path):
        return re.sub(r'/\*.*?\*/|//[^\n]*', '', Path(path).read_text(), flags=re.DOTALL)
    number = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
    points = [tuple(float(v) for v in row) for row in re.findall(r'\(\s*('+number+r')\s+('+number+r')\s+('+number+r')\s*\)', clean(Path(case)/'constant/polyMesh/points'))]
    faces = [[int(v) for v in vertices.split()] for size, vertices in re.findall(r'(\d+)\s*\(([\d\s]+)\)', clean(Path(case)/'constant/polyMesh/faces'))]
    boundary = re.search(r'\bwest\s*\{([^}]+)\}', clean(Path(case)/'constant/polyMesh/boundary'))
    if boundary is None:
        raise ValueError('No west inlet patch')
    start = int(re.search(r'\bstartFace\s+(\d+)', boundary[1])[1])
    count = int(re.search(r'\bnFaces\s+(\d+)', boundary[1])[1])
    selected = faces[start:start+count]
    centres = [sum(points[i][1] for i in face)/len(face) for face in selected]
    if count != spec['ny'] or len(centres) != count or any(abs(y-(i+.5)*.1/count)>1e-9 for i,y in enumerate(centres)):
        raise ValueError('Couette nonuniform inlet values do not match actual generated face ordering')
    return dict(west_face_y_centres=centres, face_order_verified=True,
                expected_inlet_x_velocity=[spec['wall_speed']*y/.1 for y in centres])


def field_values(payload, count, components):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', payload.decode('utf-8'), flags=re.DOTALL)
    number = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
    uniform = re.search(r'\binternalField\s+uniform\s+([^;]+);', text)
    if uniform:
        values = [float(x) for x in re.findall(number, uniform[1])]
        if len(values) != components:
            raise ValueError('Unexpected uniform field components')
        values *= count
    else:
        field = re.search(r'\binternalField\s+nonuniform\s+List<\w+>\s+(\d+)\s*\((.*?)\)\s*;', text, re.DOTALL)
        if field is None or int(field[1]) != count:
            raise ValueError('Unexpected nonuniform field cell count')
        values = [float(x) for x in re.findall(number, field[2])]
    if len(values) != count*components or not all(math.isfinite(x) for x in values):
        raise ValueError('Invalid field values')
    # Reject tokens the numeric expression might otherwise silently skip.
    body = uniform[1] if uniform else field[2]
    if re.sub(number + r'|[\s()]', '', body):
        raise ValueError('Unknown/nonfinite field token')
    return values


def stationary_fields(case, spec, context):
    record = current_record(case, context)
    times = record.data['output_times']
    if len(times) < 2:
        return dict(field_stationary=None, velocity_change_scaled=None, pressure_change_scaled=None, compared_times=[])
    scales = task(spec)['reference_contract']
    values = {}
    for field, components, scale in (('U',3,scales['velocity_scale']), ('p',1,scales['kinematic_pressure_scale'])):
        snapshots = [field_values(read_verified_result(record, t+'/'+field, context['run_id'], context['input_version']),
                                 spec['nx']*spec['ny'], components) for t in times[-2:]]
        values[field] = max(abs(a-b) for a,b in zip(*snapshots))/scale
    return dict(field_stationary=all(v <= scales['field_change_tolerance'] for v in values.values()),
                velocity_change_scaled=values['U'], pressure_change_scaled=values['p'], compared_times=times[-2:])


class ReferenceTaskCompletion(GatedChannelCompletion):
    def __init__(self, recorder, reference, image, spec):
        super().__init__(recorder, reference, image)
        self.spec = validate_spec(spec)

    def measure_outputs(self, profile, pressure):
        return reference_measurements(profile, pressure, self.spec)

    def assess_inputs(self, snapshot):
        """Legacy strict policy; new policies must explicitly override this hook."""
        checks = {}
        for folder in ('0', 'constant', 'system'):
            for p in (self.reference/folder).rglob('*'):
                if p.is_file():
                    rel = p.relative_to(self.reference)
                    checks[str(rel)] = (snapshot/rel).is_file() and canonical_dictionary(snapshot/rel) == canonical_dictionary(p)
        return checks, None

    def __call__(self, state):
        # Task-specific steady evidence must not relabel the original SIMPLE
        # marker, change the old channel policy or omit failed sample metrics.
        recorder = self.recorder
        context = state.get('simclinic_context') or {}
        case = Path(state.get('case_dir') or recorder.directory/'missing-case')
        native = case/'.simclinic/native'/context.get('run_id','missing')
        logs = recorder.directory/'attempts'/context.get('run_id','missing')/'logs'
        execution = json.loads((native/'execution.json').read_text()) if (native/'execution.json').is_file() else {}
        log = (logs/'log.simpleFoam').read_text() if (logs/'log.simpleFoam').is_file() else ''
        values = {c['id']: None for c in recorder.data['task']['acceptance']}
        values.update(solver_measurements(log, execution.get('exit_code')))
        values.update(steady_evidence=values['steady_converged'], task_inputs_verified=False, rendered_velocity=False,
                      field_stationary=None, velocity_change_scaled=None, pressure_change_scaled=None)
        checks, error = {}, None
        snapshot = native/'input-snapshot'
        checks, input_comparison = self.assess_inputs(snapshot)
        values['task_inputs_verified'] = bool(checks) and all(checks.values())
        if input_comparison is not None and checks and not any(v is False for v in checks.values()) and any(v is None for v in checks.values()):
            values['task_inputs_verified'] = None
        if not state.get('error_logs'):
            try:
                record = current_record(case, context)
                stability = stationary_fields(case, self.spec, context)
                path = recorder.directory/'observations/field-stationarity.json'
                write_json(path, stability)
                source = recorder.artifact(path, context)
                recorder.event('observation', context=context, observation_type='verified_full_field_stationarity', source_artifact=source)
                values.update({k:stability[k] for k in ('field_stationary','velocity_change_scaled','pressure_change_scaled')})
                values['steady_evidence'] = values['steady_converged'] or stability['field_stationary'] is True
                if self.spec['kind'] == 'couette':
                    checked = couette_inlet_order(case, self.spec)
                    path = recorder.directory/'observations/inlet-face-order.json'
                    write_json(path, checked)
                    source = recorder.artifact(path, context)
                    recorder.event('observation', context=context, observation_type='real_inlet_face_order', source_artifact=source)
                images = state.get('plot_outputs', [])
                artifacts = [a for a in record.data['artifacts'] if a.get('role') == 'visualization']
                values['rendered_velocity'] = bool(state.get('pyvista_visualization', {}).get('success')) and \
                    state.get('visualization_summary', {}).get('fields_visualized') == ['U'] and len(images)==1 and any(
                    (case/a['path']).resolve()==Path(images[0]).resolve() and hashlib.sha256(Path(images[0]).read_bytes()).hexdigest()==a['sha256'] for a in artifacts)
                with verified_workspace(case, context) as work:
                    measured = self.sample(work, context)
                current_record(case, context)
                values.update(measured)
            except (ResultRejected,OSError,ValueError) as exc:
                error = dict(type=type(exc).__name__, code=getattr(exc,'code','reference_assessment_error'),
                             message=str(exc), assessment_artifact=getattr(exc,'assessment_artifact',None))
                recorder.references_complete=False
        accepted = error is None and all(matches(values[c['id']],c) is True for c in recorder.data['task']['acceptance'])
        assessment = dict(**{k:context.get(k) for k in ('run_id','input_version')}, values=values, input_checks=checks,
            criteria=recorder.data['task']['acceptance'], accepted=accepted, error=error,
            reference_contract=recorder.data['task']['reference_contract'],
            method='Analytic reference plus unchanged SIMPLE marker or predeclared verified full-field stationarity; no legacy criterion relabelled.')
        if input_comparison is not None:
            assessment['input_comparison'] = input_comparison
        path=recorder.directory/'observations/task-assessment.json'
        write_json(path,assessment)
        source=recorder.artifact(path,context)
        for criterion in recorder.data['task']['acceptance']:
            recorder.event('measurement',context=context,criterion_id=criterion['id'],value=values[criterion['id']],artifact_id=source)
        recorder.acceptance_complete=True
        claim=dict(**{k:context.get(k) for k in ('run_id','input_version')},success=accepted,
            status='completed' if accepted else 'failed' if state.get('error_logs') or error else 'acceptance_not_met',
            emitter='Foam-Agent registered SimClinic analytic reference completion node', assessment_artifact=source,criteria_preserved=True)
        write_json(recorder.directory/'observations/completion-claim.json',claim)
        return dict(acceptance_report=assessment,completion_claim=claim)
