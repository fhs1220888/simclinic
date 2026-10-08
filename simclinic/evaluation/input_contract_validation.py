"""Predeclared authored development controls; never heldout optimization data."""
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
import re

from ..domains.openfoam.input_contract import FILES

# Exactly one declared file is changed in each negative. These are deliberate
# contract violations, not suggested simulation improvements.
NEGATIVES = {
    '0/U': ('(0.000478260869565 0 0)', '(0.000488260869565 0 0)'),
    '0/p': ('internalField uniform 0;', 'internalField uniform 0.00000000000001;'),
    'constant/physicalProperties': ('nu 0.001;', 'nu 0.00100000000001;'),
    'constant/momentumTransport': ('simulationType laminar;', 'simulationType RAS;'),
    'system/blockMeshDict': ('(46 23 1)', '(48 23 1)'),
    'system/controlDict': ('writePrecision 12;', 'writePrecision 10;'),
    'system/fvSchemes': ('default steadyState;', 'default Euler;'),
    'system/fvSolution': ('tolerance 1e-10;', 'tolerance 1.0001e-10;'),
    'system/sample': ('nPoints 20;', 'nPoints 21;'),
}

DEVELOPMENT_SPECS = (
    dict(id='C3-development', kind='couette', wall_speed=0.022, nx=46, ny=23),
    dict(id='C4-development', kind='couette', wall_speed=0.035, nx=52, ny=26),
)


def high_precision_inlet(path, spec):
    path = Path(path)
    with localcontext() as context:
        context.prec = 40
        values = []
        for i in range(spec['ny']):
            exact = Fraction(str(spec['wall_speed'])) * Fraction(2*i+1, 2*spec['ny'])
            values.append('('+str(Decimal(exact.numerator)/Decimal(exact.denominator))+' 0 0)')
    text, count = re.subn(r'(value nonuniform List<vector> \d+ )\([^;]+\)',
                         lambda m: m[1]+'('+' '.join(values)+')', path.read_text())
    if count!=1: raise ValueError('Expected one literal west inlet')
    path.write_text(text)


def positive_text(text, relative):
    # Keep integer reader labels unchanged. Only specifically declared scalar
    # literals change notation; this is not an arbitrary token rewriter.
    text = text.replace('format ascii;', '/* harmless header comment */ format ascii;')
    text = text.replace('object '+Path(relative).name+';',
                        'object '+Path(relative).name+'; location "'+str(Path(relative).parent)+'";')
    substitutions = {
        '0/U': ('internalField uniform (0 0 0)', 'internalField uniform (0e0 0.00 -0.0)'),
        '0/p': ('internalField uniform 0;', 'internalField uniform 0e0;'),
        'constant/physicalProperties': ('nu 0.001;', 'nu [0 2 -1 0 0 0 0] 1e-3;'),
        'system/blockMeshDict': ('convertToMeters 1;', 'convertToMeters 1.0e0;'),
        'system/controlDict': ('deltaT 1;', 'deltaT 1e0;'),
        'system/fvSolution': ('tolerance 1e-10;', 'tolerance 0.0000000001;'),
        'system/sample': ('(0.5 0.0025 0.005)', '(5e-1 25e-4 5e-3)'),
    }
    if relative in substitutions:
        old, new = substitutions[relative]
        if old not in text: raise ValueError('Missing positive literal: '+relative)
        text = text.replace(old, new)
    return '// Authored equivalent development representation\n'+text+'\n/* trailing comment */\n'


def apply_positive(case, spec, high_precision=False):
    case = Path(case)
    for name in FILES:
        p = case/name; p.write_text(positive_text(p.read_text(), name))
    if high_precision: high_precision_inlet(case/'0/U', spec)


def apply_negative(case, relative):
    old, new = NEGATIVES[relative]
    p = Path(case)/relative; text = p.read_text()
    if old not in text: raise ValueError('Missing negative literal: '+relative)
    p.write_text(text.replace(old, new, 1))


def schedule():
    entries = []
    for spec in DEVELOPMENT_SPECS:
        for variant in ('template', 'equivalent_high_precision'):
            entries.append(dict(id=spec['id']+'-'+variant, variant=variant, spec=spec,
                                changed_file=None, expected_quality=True, expected_input_match=True,
                                expected_solver_end=True))
    for name in FILES:
        entries.append(dict(id='negative-'+name.replace('/','-'), variant='changed_literal', spec=DEVELOPMENT_SPECS[0],
                            changed_file=name, expected_quality=False, expected_input_match=False,
                            expected_solver_end=name!='constant/momentumTransport'))
    entries.append(dict(id='negative-wrong-nu-dimensions', variant='wrong_dimensions', spec=DEVELOPMENT_SPECS[0],
                        changed_file='constant/physicalProperties', expected_quality=False,
                        expected_input_match=False, expected_solver_end=False))
    return entries
