"""Opt-in literal contract for the nine authored Foundation 10 Couette files.

This is a restricted dictionary reader, not an OpenFOAM interpreter. Numbers
remain exact rationals. Only west Ux admits a declared serialization interval;
byte hashes, result identity and CFD acceptance thresholds remain independent.
"""
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from .quantity_inputs import DIMENSIONS, NUMBER, SOURCES as NU_SOURCES, Unresolved
from .reference_tasks import ReferenceTaskCompletion, task, validate_spec
from .sampling_recovery import BoundedSamplingRecovery

POLICY = 'foundation10-nine-file-couette-literals-v3'
SIGNIFICANT_DIGITS = 12
SOURCES = NU_SOURCES + ['https://cpp.openfoam.org/v10/ISstream_8C_source.html']
FILES = ('0/U', '0/p', 'constant/momentumTransport', 'constant/physicalProperties',
         'system/blockMeshDict', 'system/controlDict', 'system/fvSchemes',
         'system/fvSolution', 'system/sample')


@dataclass(frozen=True)
class Numeric:
    value: Fraction
    raw: str = field(compare=False)


@dataclass(frozen=True)
class Quoted:
    value: str


@dataclass(frozen=True)
class Group:
    delimiter: str
    items: tuple


@dataclass(frozen=True)
class Named:
    name: str
    body: dict


def lex(text):
    if len(text) > 1_000_000:
        raise Unresolved('input_too_large')
    result, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text.startswith('//', pos):
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
        elif text.startswith('/*', pos):
            end = text.find('*/', pos+2)
            if end < 0:
                raise Unresolved('unterminated_comment')
            pos = end+2
        elif text[pos] in "#$'":
            raise Unresolved('dynamic_or_unsupported_syntax')
        elif text[pos] == '"':
            match = re.match(r'"(?:[^"\\]|\\.)*"', text[pos:])
            if not match:
                raise Unresolved('unterminated_string')
            try:
                result.append(Quoted(json.loads(match[0])))
            except ValueError:
                raise Unresolved('unsupported_string_escape')
            pos += len(match[0])
        elif text[pos] in '{};()[]':
            result.append(text[pos]); pos += 1
        else:
            match = re.match(r'[^\s{};()\[\]"#$\']+', text[pos:])
            if not match:
                raise Unresolved('unsupported_token')
            word = match[0]; pos += len(word)
            # The declared div-scheme keys contain balanced parentheses.
            if word == 'div' and pos < len(text) and text[pos] == '(':
                start, depth = pos, 0
                while pos < len(text):
                    char = text[pos]
                    if char.isspace() or char in '{};[]"#$\'':
                        raise Unresolved('unsupported_compound_key')
                    depth += (char == '(') - (char == ')')
                    pos += 1
                    if depth == 0:
                        break
                if depth:
                    raise Unresolved('unbalanced_compound_key')
                word += text[start:pos]
            if re.fullmatch(NUMBER, word):
                # Foundation 10 tokenizes a leading '+' as punctuation, not
                # as part of a scalar. Exponent signs remain valid.
                if word.startswith('+'):
                    raise Unresolved('unsupported_leading_plus_literal')
                if len(word) > 128 or ('e' in word.lower() and abs(int(word.lower().split('e')[1])) > 100):
                    raise Unresolved('numeric_literal_out_of_scope')
                result.append(Numeric(Fraction(word), word))
            else:
                if word.lower() in ('nan', 'inf', 'infinity', '-inf', '+inf'):
                    raise Unresolved('nonfinite_literal')
                result.append(word)
        if len(result) > 100_000:
            raise Unresolved('too_many_tokens')
    return result


class Reader:
    def __init__(self, tokens):
        self.tokens, self.pos = tokens, 0

    def take(self):
        if self.pos == len(self.tokens):
            raise Unresolved('unexpected_end')
        value = self.tokens[self.pos]; self.pos += 1
        return value

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def dictionary(self, closing=None, depth=0):
        if depth > 50:
            raise Unresolved('excessive_nesting')
        entries = {}
        while self.peek() != closing:
            key = self.take()
            if not isinstance(key, str) or key in '{};()[]':
                raise Unresolved('unsupported_entry_key')
            if key in entries:
                raise Unresolved('duplicate_entry:' + key)
            if self.peek() == '{':
                self.take()
                value = self.dictionary('}', depth+1)
                if self.peek() == ';': self.take()
            else:
                parts = []
                while self.peek() != ';':
                    if self.peek() in (None, '}'):
                        raise Unresolved('missing_entry_terminator')
                    parts.append(self.item(depth+1))
                self.take(); value = tuple(parts)
            entries[key] = value
        if closing is not None: self.take()
        return entries

    def item(self, depth):
        if depth > 50: raise Unresolved('excessive_nesting')
        token = self.take()
        if token in ('(', '['):
            end = ')' if token == '(' else ']'
            items, names = [], set()
            while self.peek() != end:
                if self.peek() is None: raise Unresolved('unclosed_group')
                item = self.item(depth+1)
                if isinstance(item, Named):
                    if item.name in names: raise Unresolved('duplicate_named_entry:' + item.name)
                    names.add(item.name)
                items.append(item)
            self.take()
            return Group(token, tuple(items))
        if isinstance(token, str) and self.peek() == '{':
            self.take()
            return Named(token, self.dictionary('}', depth+1))
        if isinstance(token, str) and token in '{};)]':
            raise Unresolved('unexpected_delimiter')
        return token


def literal_dictionary(text, relative):
    reader = Reader(lex(text))
    body = reader.dictionary()
    header = body.pop('FoamFile', None)
    if not isinstance(header, dict): raise Unresolved('missing_dictionary_header')
    if set(header)-{'version', 'format', 'class', 'object', 'location'}:
        raise Unresolved('unsupported_header_entry')
    location = header.pop('location', None)
    if location is not None and location != (Quoted(str(Path(relative).parent)),):
        raise Unresolved('unsupported_header_location')
    # Metadata that controls how the reader interprets the field stays checked.
    return header, body


def shape_supported(actual, expected):
    """Unknown additions/expressions cannot be classified as equivalent literals."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict): raise Unresolved('unsupported_value_shape')
        if set(actual)-set(expected): raise Unresolved('undeclared_dictionary_entry')
        for key in set(actual)&set(expected): shape_supported(actual[key], expected[key])
    elif isinstance(expected, tuple):
        if not isinstance(actual, tuple): raise Unresolved('unsupported_value_shape')
        for a, b in zip(actual, expected): shape_supported(a, b)
    elif isinstance(expected, Group):
        if not isinstance(actual, Group): raise Unresolved('unsupported_value_shape')
        for a, b in zip(actual.items, expected.items): shape_supported(a, b)
    elif isinstance(expected, Named):
        if not isinstance(actual, Named): raise Unresolved('unsupported_named_dictionary')
        shape_supported(actual.body, expected.body)
    elif isinstance(expected, Numeric) and not isinstance(actual, Numeric):
        raise Unresolved('unsupported_numeric_expression')
    elif isinstance(actual, Quoted) and not isinstance(expected, Quoted):
        raise Unresolved('unsupported_quoted_value')


def precision_bound(value):
    """Half of one unit at 12 significant digits, using only rational math."""
    value = abs(value)
    if not value: return Fraction(0)
    exponent = 0
    while value < 1: value *= 10; exponent -= 1
    while value >= 10: value /= 10; exponent += 1
    quantum = Fraction(10) ** (exponent-SIGNIFICANT_DIGITS+1)
    return quantum/2


def label(value):
    return isinstance(value, Numeric) and re.fullmatch(r'-?\d+', value.raw) is not None


def validate_labels(body, relative):
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('nPoints', 'purgeWrite', 'writePrecision', 'timePrecision', 'nNonOrthogonalCorrectors'):
                    if not isinstance(value, tuple) or len(value)!=1 or not label(value[0]):
                        raise Unresolved('unsupported_label_literal:' + key)
                walk(value)
        elif isinstance(node, Named): walk(node.body)
        elif isinstance(node, Group):
            for item in node.items: walk(item)
        elif isinstance(node, tuple):
            for item in node: walk(item)
    walk(body)
    if relative == 'system/blockMeshDict' and 'blocks' in body:
        try:
            items = body['blocks'][0].items
            for group in (items[1], items[2]):
                if not all(label(n) for n in group.items): raise Unresolved('unsupported_mesh_label')
            for patch in body['boundary'][0].items:
                for face in patch.body['faces'][0].items:
                    if not all(label(n) for n in face.items): raise Unresolved('unsupported_face_label')
        except (AttributeError, IndexError, KeyError):
            raise Unresolved('unsupported_mesh_shape')


def normalize_nu(body):
    if 'nu' not in body: return
    value = body['nu']
    if len(value)==1 and isinstance(value[0], Numeric):
        body['nu'] = (Group('[', tuple(Numeric(Fraction(x), str(x)) for x in DIMENSIONS)), value[0])
    elif len(value)!=2 or not isinstance(value[0], Group) or value[0].delimiter!='[' or not isinstance(value[1], Numeric):
        raise Unresolved('unsupported_nu_literal')
    elif len(value[0].items)!=7 or not all(isinstance(n, Numeric) for n in value[0].items):
        raise Unresolved('unsupported_dimension_vector')


def inlet_review(body, spec):
    try:
        west = body['boundaryField']['west']
        value = west['value']
        if west['type'] != ('fixedValue',) or len(value)!=4 or value[:2] != ('nonuniform', 'List<vector>'):
            return False, {'reason': 'inlet_type_or_shape_changed'}
        count, vectors = value[2:]
        if not label(count): raise Unresolved('unsupported_inlet_count_literal')
        if count.value != spec['ny'] or not isinstance(vectors, Group) or vectors.delimiter!='(' or len(vectors.items)!=spec['ny']:
            return False, {'reason': 'inlet_count_changed'}
        rows, ok = [], True
        for i, vector in enumerate(vectors.items):
            if not isinstance(vector, Group) or vector.delimiter!='(' or len(vector.items)!=3 or not all(isinstance(n, Numeric) for n in vector.items):
                raise Unresolved('unsupported_inlet_vector')
            exact = Fraction(str(spec['wall_speed'])) * Fraction(2*i+1, 2*spec['ny'])
            bound = precision_bound(exact)
            error = abs(vector.items[0].value-exact)
            matched = error <= bound and vector.items[1].value==0 and vector.items[2].value==0
            ok = ok and matched
            rows.append(dict(face_index=i, literal=vector.items[0].raw, exact_formula_value=str(exact),
                             absolute_error=str(error), maximum_serialization_error=str(bound), matched=matched))
        # Replace only the validated Ux slots; transverse components still compare exactly.
        normalized = tuple(Group('(', ('declared_west_ux',)+v.items[1:]) for v in vectors.items)
        west['value'] = value[:3] + (Group('(', normalized),)
        return ok, dict(reason='formula_within_serialization_bound' if ok else 'inlet_quantity_changed',
                        formula='wall_speed * (2*face_index+1) / (2*ny)', significant_digits=SIGNIFICANT_DIGITS,
                        bound_rule='half_unit_at_12_significant_digits_of_exact_nonzero_value_inclusive', faces=rows)
    except (KeyError, TypeError, AttributeError):
        return False, {'reason': 'missing_or_changed_inlet'}


def compare_input(actual, expected, relative, spec):
    actual, expected, relative = Path(actual), Path(expected), str(relative)
    validate_spec(spec)
    if spec['kind']!='couette': raise ValueError('This policy supports declared Couette only')
    evidence = dict(policy=POLICY, relative_path=relative, actual=str(actual), expected=str(expected),
                    actual_sha256=None, expected_sha256=hashlib.sha256(expected.read_bytes()).hexdigest(),
                    numeric_rule='exact_decimal_rational_except_declared_west_ux', source_basis=SOURCES)
    if relative not in FILES:
        return dict(evidence, status='not_applicable', matched=None, reason='outside_nine_file_contract')
    if not actual.is_file() or any(p.is_symlink() for p in (actual,)+tuple(actual.parents)[:len(Path(relative).parts)]):
        return dict(evidence, status='insufficient_evidence', matched=None, reason='missing_or_symlink_input')
    evidence['actual_sha256'] = hashlib.sha256(actual.read_bytes()).hexdigest()
    try:
        wanted_header, wanted = literal_dictionary(expected.read_text(), relative)
        if relative=='constant/physicalProperties': normalize_nu(wanted)
        validate_labels(wanted, relative)
        if relative=='0/U':
            trusted_ok, _ = inlet_review(wanted, spec)
            if not trusted_ok: raise ValueError('Reference inlet violates declared formula/serialization contract')
    except (Unresolved, UnicodeError) as exc:
        raise ValueError('Unsupported trusted input contract: '+str(exc))
    try:
        header, found = literal_dictionary(actual.read_text(), relative)
        if relative=='constant/physicalProperties': normalize_nu(found)
        validate_labels(found, relative)
        inlet_ok = True
        if relative=='0/U':
            inlet_ok, evidence['inlet'] = inlet_review(found, spec)
        shape_supported(header, wanted_header)
        shape_supported(found, wanted)
    except (Unresolved, UnicodeError, ValueError) as exc:
        return dict(evidence, status='insufficient_evidence', matched=None, reason=str(exc))
    matched = inlet_ok and header==wanted_header and found==wanted
    return dict(evidence, status='pass' if matched else 'finding', matched=matched,
                reason='declared_literals_match' if matched else 'declared_input_changed')


def compare_case(snapshot, reference, spec):
    details = {name: compare_input(Path(snapshot)/name, Path(reference)/name, name, spec) for name in FILES}
    # A newly introduced input may participate in reader behavior; ignoring it
    # would falsely establish a complete nine-file contract.
    for folder in ('0', 'constant', 'system'):
        base = Path(snapshot)/folder
        for path in sorted(base.rglob('*')):
            name = str(path.relative_to(snapshot))
            if (path.is_file() or path.is_symlink()) and name not in FILES:
                details[name] = dict(policy=POLICY, relative_path=name, actual=str(path),
                                     status='insufficient_evidence', matched=None, reason='undeclared_input_file')
    return {k: v['matched'] for k, v in details.items()}, dict(policy=POLICY, files=details)


def contract_task(spec):
    validate_spec(spec)
    if spec['kind']!='couette': raise ValueError('Couette required')
    declared = task(spec)
    declared['reference_contract'].update(input_matching_policy=POLICY, inlet_significant_digits=SIGNIFICANT_DIGITS)
    declared['objective'] += ('\nInput policy '+POLICY+': literal dictionaries only. Decimal/scientific scalar notation and comments may vary; all other physical and numerical values remain exact. Integer labels remain integer literals. Constant nu=0.001 may omit dimensions or state [0 2 -1 0 0 0 0]. West Ux at face i must equal wall_speed*(2*i+1)/(2*ny), within half a unit of the exact value at 12 significant digits; higher precision is allowed. Uy=Uz=0 exactly. Header class/object/format stay checked. Unknown directives, expressions or duplicate entries give insufficient evidence. Raw bytes still define input identity.\n')
    return declared


class ContractReferenceCompletion(ReferenceTaskCompletion):
    def assess_inputs(self, snapshot):
        declared = self.recorder.data['task']['reference_contract']
        if declared.get('input_matching_policy')!=POLICY or declared.get('inlet_significant_digits')!=SIGNIFICANT_DIGITS:
            raise ValueError('Nine-file matching requires the explicit versioned task contract')
        return compare_case(snapshot, self.reference, self.spec)


class RecoveringContractReferenceCompletion(BoundedSamplingRecovery, ContractReferenceCompletion):
    pass
