"""Explicit Foundation 10 constant-nu literal policy, never a dictionary interpreter.

Only this property admits optional matching dimensions; all other files retain
legacy canonical comparison. Unknown syntax cannot establish task completion.
"""
from decimal import Decimal
import hashlib
from pathlib import Path
import re

from .completion import canonical_dictionary
from .reference_tasks import ReferenceTaskCompletion, task
from .sampling_recovery import BoundedSamplingRecovery

POLICY = 'foundation10-constant-nu-literal-v1'
DIMENSIONS = (0, 2, -1, 0, 0, 0, 0)
NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
SOURCES = [
    'https://cpp.openfoam.org/v10/constantViscosityModel_8C_source.html',
    'https://cpp.openfoam.org/v10/dimensionedType_8C_source.html',
]


class Unresolved(ValueError):
    pass


def property_literal(text):
    # Comments are lexed before stripping, so quoted or dynamic syntax cannot
    # hide an executable directive. This is intentionally a small ASCII grammar.
    cleaned = []
    pos = 0
    while pos < len(text):
        if text.startswith('//', pos):
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
        elif text.startswith('/*', pos):
            end = text.find('*/', pos + 2)
            if end < 0:
                raise Unresolved('unterminated_comment')
            cleaned.append(' ')
            pos = end + 2
        else:
            if text[pos] in '\"\'#$':
                raise Unresolved('quoted_or_dynamic_syntax')
            cleaned.append(text[pos])
            pos += 1
    body = ''.join(cleaned)
    body = re.sub(r'\bFoamFile\s*\{[^{}]*\}', '', body, count=1)
    entries = re.findall(r'\b(viscosityModel|nu)\s+([^;]+);', body)
    if len(entries) != 2 or {k for k, v in entries} != {'viscosityModel', 'nu'}:
        raise Unresolved('missing_or_duplicate_property')
    if re.sub(r'\b(viscosityModel|nu)\s+[^;]+;', '', body).strip():
        raise Unresolved('unsupported_dictionary_entries')
    values = dict(entries)
    model = values['viscosityModel'].strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', model):
        raise Unresolved('unsupported_model_expression')
    literal = re.fullmatch(r'\s*(?:\[([^\[\]]+)\]\s*)?(' + NUMBER + r')\s*', values['nu'])
    if literal is None:
        raise Unresolved('unsupported_nu_literal')
    dims = DIMENSIONS
    explicit = literal[1] is not None
    if explicit:
        parts = literal[1].split()
        if len(parts) != 7 or any(not re.fullmatch(NUMBER, x) for x in parts):
            raise Unresolved('unsupported_dimension_vector')
        dims = tuple(Decimal(x) for x in parts)
    value = Decimal(literal[2])
    return model, dims, value, explicit


def compare_input(actual, expected, relative):
    """Return four-state evidence and an exact property value comparison."""
    actual, expected = Path(actual), Path(expected)
    result = dict(policy=POLICY, relative_path=str(relative), scope='constant/physicalProperties.nu' if str(relative)=='constant/physicalProperties' else 'legacy_canonical_tokens',
                  actual=str(actual), expected=str(expected), actual_sha256=None,
                  expected_sha256=hashlib.sha256(expected.read_bytes()).hexdigest())
    if not actual.is_file() or actual.is_symlink():
        return dict(result, status='insufficient_evidence', matched=None, reason='missing_or_symlink_input')
    result['actual_sha256'] = hashlib.sha256(actual.read_bytes()).hexdigest()
    if str(relative) != 'constant/physicalProperties':
        matched = canonical_dictionary(actual) == canonical_dictionary(expected)
        return dict(result, status='pass' if matched else 'finding', matched=matched, reason='canonical_tokens_equal' if matched else 'canonical_tokens_differ')
    try:
        wanted = property_literal(expected.read_text())
    except (Unresolved, UnicodeError) as exc:
        raise ValueError('Unsupported trusted property contract: ' + str(exc))
    try:
        found = property_literal(actual.read_text())
    except (Unresolved, UnicodeError) as exc:
        return dict(result, status='insufficient_evidence', matched=None, reason=str(exc))
    model, dims, value, explicit = found
    result.update(actual_nu=str(value), expected_nu=str(wanted[2]), explicit_dimensions=explicit,
                  actual_dimensions=[str(x) for x in dims], required_dimensions=list(DIMENSIONS), source_basis=SOURCES)
    if wanted[0] != 'constant' or wanted[1] != DIMENSIONS or wanted[2] <= 0:
        raise ValueError('Unsupported trusted viscosity contract')
    reason = ('viscosity_model_changed' if model != wanted[0] else
              'wrong_dimensions' if dims != DIMENSIONS else
              'nu_value_changed' if value != wanted[2] or value <= 0 else 'same_quantity')
    matched = reason == 'same_quantity'
    return dict(result, status='pass' if matched else 'finding', matched=matched, reason=reason)


def compare_case(snapshot, reference):
    details = {}
    for folder in ('0', 'constant', 'system'):
        for p in sorted((Path(reference)/folder).rglob('*')):
            if p.is_file():
                rel = p.relative_to(reference)
                details[str(rel)] = compare_input(Path(snapshot)/rel, p, rel)
    return {k: v['matched'] for k, v in details.items()}, dict(policy=POLICY, files=details)


def quantity_task(spec):
    declared = task(spec)
    declared['objective'] += ('\nInput matching policy ' + POLICY + ': for constant viscosity, nu may be a positive scalar literal with omitted dimensions or explicit [0 2 -1 0 0 0 0]. Both must equal 0.001 exactly as a decimal number. No expression, directive, duplicate entry or other property is accepted by this limited verifier. All other dictionary matching and acceptance thresholds are unchanged.\n')
    declared['reference_contract']['input_matching_policy'] = POLICY
    return declared


class QuantityReferenceCompletion(ReferenceTaskCompletion):
    def assess_inputs(self, snapshot):
        declared = self.recorder.data['task']['reference_contract']
        if declared.get('input_matching_policy') != POLICY:
            raise ValueError('Quantity matching requires an explicit task contract')
        return compare_case(snapshot, self.reference)


class RecoveringQuantityReferenceCompletion(BoundedSamplingRecovery, QuantityReferenceCompletion):
    pass
