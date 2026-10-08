"""Predeclared channel tasks and frozen-archive verification, independent of RAG."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from ..domains.openfoam.acceptance import projected_rows
from ..domains.openfoam.completion import ChannelCompletion, task_definition

TASKS = [dict(id='H1', mean_velocity=0.015, nx=40, ny=20),
         dict(id='H2', mean_velocity=0.025, nx=40, ny=20),
         dict(id='H3', mean_velocity=0.015, nx=60, ny=30)]
DEVELOPMENT = dict(id='D0', mean_velocity=0.01, nx=40, ny=20)


def hashes(directory):
    directory = Path(directory)
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify_hashes(directory, expected):
    actual = hashes(directory)
    if actual != expected:
        raise ValueError('Frozen files changed: ' + str(directory))


def requirement(template, spec):
    text = template.replace('mesh 40 x 20 x 1', f"mesh {spec['nx']} x {spec['ny']} x 1")
    text = text.replace('streamwise mean velocity 0.01 m/s', f"streamwise mean velocity {spec['mean_velocity']} m/s")
    text = text.replace('of 20 values at y=(i+0.5)*0.005',
                        f"of {spec['ny']} values at y=(i+0.5)*0.1/{spec['ny']}")
    text = text.replace('Ux=6*0.01*', f"Ux=6*{spec['mean_velocity']}*")
    return text.replace('Use the retrieved reference numerical dictionaries and boundary names exactly.',
        'Use the retrieved reference numerical dictionaries and boundary names exactly.\n'
        f"Adapt only mesh block counts, inlet list length/values, and U internalField to\n"
        f"the above explicit task: internalField uniform ({spec['mean_velocity']} 0 0).\n"
        'The retrieved 0.01 m/s, 40 x 20 template is a development example; the task\n'
        'parameters above take precedence. Keep the reference sampling points unchanged.')


def create_reference(template_case, destination, spec):
    destination = Path(destination)
    shutil.copytree(template_case, destination)
    path = destination / 'system/blockMeshDict'
    text = path.read_text()
    if '(40 20 1)' not in text:
        raise ValueError('Development template mesh changed')
    path.write_text(text.replace('(40 20 1)', f"({spec['nx']} {spec['ny']} 1)"))
    path = destination / '0/U'
    text = path.read_text()
    values = ' '.join(f"({6*spec['mean_velocity']*((i+0.5)/spec['ny'])*(1-(i+0.5)/spec['ny']):.12g} 0 0)"
                      for i in range(spec['ny']))
    text, n = re.subn(r'internalField uniform \(0\.01 0 0\)',
                     f"internalField uniform ({spec['mean_velocity']} 0 0)", text)
    if n != 1:
        raise ValueError('Development template U internalField changed')
    text, n = re.subn(r'value nonuniform List<vector> 20 \(.*?\);',
                     f"value nonuniform List<vector> {spec['ny']} ({values});", text, flags=re.DOTALL)
    if n != 1:
        raise ValueError('Development template inlet changed')
    path.write_text(text)


def task(spec, objective):
    result = task_definition(objective)
    result['id'] = 'poiseuille-channel-' + spec['id']
    return result


def velocity_error(path, spec):
    values = projected_rows(path, 'y', ('U_x', 'U_y', 'U_z'), 4)
    if len(values) != 20 or any(abs(row[0] - (0.0025+i*0.005)) > 1e-6 for i, row in enumerate(values)):
        raise ValueError('Velocity sampling points differ from fixed task')
    expected = [6*spec['mean_velocity']*(row[0]/0.1)*(1-row[0]/0.1) for row in values]
    numerator = sum((row[1]-u)**2+row[2]**2+row[3]**2 for row, u in zip(values, expected))
    return math.sqrt(numerator / sum(u*u for u in expected))


def pressure_error(path, spec):
    values = projected_rows(path, 'x', ('p',), 2)
    if len(values) != 17 or any(abs(row[0] - (0.1+i*0.05)) > 1e-6 for i, row in enumerate(values)):
        raise ValueError('Pressure sampling points differ from fixed task')
    xs, ps = [row[0] for row in values], [row[1] for row in values]
    xm, pm = sum(xs)/len(xs), sum(ps)/len(ps)
    slope = sum((x-xm)*(p-pm) for x, p in zip(xs, ps))/sum((x-xm)**2 for x in xs)
    expected = -12*0.001*spec['mean_velocity']/0.1**2
    return abs(slope-expected)/abs(expected)


class HeldoutCompletion(ChannelCompletion):
    def __init__(self, recorder, reference, image, spec):
        super().__init__(recorder, reference, image)
        self.spec = spec

    def sample(self, work, context):
        # Shared process/byte collection, followed by the declared task's analytic reference.
        super().sample(work, context)
        directory = self.recorder.directory / 'observations/sampling'
        return dict(profile_error=velocity_error(directory/'profile.xy', self.spec),
                    pressure_gradient_error=pressure_error(directory/'pressure.xy', self.spec))
