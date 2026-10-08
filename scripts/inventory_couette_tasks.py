"""Read-only, standard-library inventory of historical Couette declarations.

This identifies declared parameters, including rejected/negative inputs; it
does not certify OpenFOAM validity or that a declaration was actually solved.
"""
import argparse
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
NUMBER = re.compile(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z')


class Unresolved(ValueError):
    pass


class Group:
    def __init__(self, delimiter, items):
        self.delimiter, self.items = delimiter, items


class Named:
    def __init__(self, name, body):
        self.name, self.body = name, body


def tokens(text):
    """Literal dictionary subset only; directives/expressions are never run."""
    output, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text.startswith('//', pos):
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
        elif text.startswith('/*', pos):
            end = text.find('*/', pos + 2)
            if end < 0:
                raise Unresolved('unterminated_comment')
            pos = end + 2
        elif text[pos] in '#$\'`':
            raise Unresolved('directive_or_expression')
        elif text[pos] == '"':
            match = re.match(r'"(?:[^"\\]|\\.)*"', text[pos:])
            if not match:
                raise Unresolved('unterminated_string')
            output.append(('quoted', json.loads(match[0])))
            pos += len(match[0])
        elif text[pos] in '{};()[]':
            output.append(text[pos]); pos += 1
        else:
            match = re.match(r'[^\s{};()\[\]"#$\'`]+', text[pos:])
            if not match:
                raise Unresolved('unsupported_token')
            word = match[0]; pos += len(word)
            if NUMBER.fullmatch(word):
                if len(word) > 128 or ('e' in word.lower() and abs(int(word.lower().split('e')[1])) > 100):
                    raise Unresolved('out_of_range_number')
                output.append(Fraction(word))
            else:
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_./:<>-]*', word):
                    raise Unresolved('expression_or_unsupported_word')
                output.append(word)
        if len(output) > 100000:
            raise Unresolved('input_too_large')
    return output


class Reader:
    def __init__(self, source):
        self.values, self.pos = tokens(source), 0

    def peek(self):
        return self.values[self.pos] if self.pos < len(self.values) else None

    def take(self):
        result = self.peek()
        if result is None:
            raise Unresolved('unexpected_end')
        self.pos += 1
        return result

    def dictionary(self, closing=None, depth=0):
        if depth > 50:
            raise Unresolved('excessive_nesting')
        output = {}
        while self.peek() != closing:
            key = self.take()
            if not isinstance(key, str) or key in '{};()[]':
                raise Unresolved('unsupported_key')
            if key in output:
                raise Unresolved('duplicate_key:' + key)
            if self.peek() == '{':
                self.take()
                value = self.dictionary('}', depth + 1)
                if self.peek() == ';':
                    self.take()
            else:
                value = []
                while self.peek() != ';':
                    if self.peek() in (None, '}'):
                        raise Unresolved('missing_terminator')
                    value.append(self.item(depth + 1))
                self.take()
            output[key] = value
        if closing is not None:
            self.take()
        return output

    def item(self, depth):
        if depth > 50:
            raise Unresolved('excessive_nesting')
        token = self.take()
        if token in ('(', '['):
            end = ')' if token == '(' else ']'
            items, names = [], set()
            while self.peek() != end:
                item = self.item(depth + 1)
                if isinstance(item, Named):
                    if item.name in names:
                        raise Unresolved('duplicate_named_key:' + item.name)
                    names.add(item.name)
                items.append(item)
            self.take()
            return Group(token, items)
        if isinstance(token, str) and self.peek() == '{':
            self.take()
            return Named(token, self.dictionary('}', depth + 1))
        if isinstance(token, str) and token in '{};)]':
            raise Unresolved('unexpected_delimiter')
        return token


def dictionary(payload):
    return Reader(payload.decode('utf-8')).dictionary()


def mesh_counts(body):
    parts = body.get('blocks')
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], Group):
        raise Unresolved('unsupported_blocks')
    block = parts[0].items
    if len(block) != 5 or block[0] != 'hex' or not isinstance(block[2], Group):
        raise Unresolved('unsupported_single_hex_mesh')
    counts = block[2].items
    if len(counts) != 3 or any(not isinstance(x, Fraction) or x.denominator != 1 or x <= 0 for x in counts):
        raise Unresolved('unresolved_mesh_count')
    return tuple(int(x) for x in counts)


def topology(body):
    boundary = body.get('boundary')
    if not isinstance(boundary, list) or len(boundary) != 1 or not isinstance(boundary[0], Group):
        raise Unresolved('unresolved_boundary')
    patches = boundary[0].items
    if any(not isinstance(patch, Named) for patch in patches):
        raise Unresolved('unresolved_patch')
    names = {patch.name for patch in patches}
    if {'west', 'east', 'south', 'north'} <= names:
        return 'reference_xy'
    if {'inlet', 'outlet', 'walls'} <= names and not {'west', 'north', 'south'} & names:
        return 'legacy_channel'
    raise Unresolved('unrecognized_mesh_topology')


def uniform_velocity(boundaries, name):
    value = boundaries.get(name, {}).get('value')
    if not isinstance(value, list) or len(value) != 2 or value[0] != 'uniform' or not isinstance(value[1], Group):
        raise Unresolved('unresolved_uniform_velocity:' + name)
    vector = value[1].items
    if len(vector) != 3 or any(not isinstance(x, Fraction) for x in vector):
        raise Unresolved('unresolved_vector:' + name)
    return tuple(vector)


def input_combination(mesh, velocity):
    """Return explicit north/south Couette parameters, or a known other flow."""
    body = dictionary(mesh)
    if topology(body) == 'legacy_channel':
        return None
    nx, ny, nz = mesh_counts(body)
    if nz != 1:
        raise Unresolved('non_reference_thickness_mesh')
    u = dictionary(velocity)
    boundaries = u.get('boundaryField')
    if not isinstance(boundaries, dict):
        raise Unresolved('unresolved_velocity_boundaries')
    north = boundaries.get('north')
    south = boundaries.get('south')
    if not isinstance(north, dict) or not isinstance(south, dict):
        raise Unresolved('missing_reference_velocity_boundary')
    # The explicitly authored other reference mechanism has outflow at north
    # and prescribed positive x/y uniform inflow at south.
    if north.get('type') == ['zeroGradient'] and south.get('type') == ['fixedValue']:
        value = uniform_velocity(boundaries, 'south')
        if value[0] > 0 and value[1] > 0 and value[2] == 0:
            return None
        raise Unresolved('unrecognized_other_flow')
    if north.get('type') != ['fixedValue'] or south.get('type') != ['fixedValue']:
        raise Unresolved('unrecognized_wall_boundary_type')
    moving = uniform_velocity(boundaries, 'north')
    fixed = uniform_velocity(boundaries, 'south')
    if fixed != (0, 0, 0) or moving[0] <= 0 or moving[1:] != (0, 0):
        raise Unresolved('unrecognized_wall_flow')
    return (moving[0], nx, ny)


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Unresolved('duplicate_json_key:' + key)
        result[key] = value
    return result


def json_objects(value, pointer=''):
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            escaped = key.replace('~', '~0').replace('/', '~1')
            yield from json_objects(child, pointer + '/' + escaped)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from json_objects(child, pointer + '/' + str(index))


def inventory(root):
    root = Path(root).resolve()
    archive = root / 'real-runs'
    if not archive.is_dir():
        raise ValueError('Expected a repository root containing real-runs')
    counts, errors, exclusions, partial_inputs, combinations, file_hashes = Counter(), [], [], [], {}, {}
    parsed = {}

    def read(path):
        if path.is_symlink():
            raise Unresolved('symlink_source')
        payload = path.read_bytes()
        name = path.relative_to(root).as_posix()
        file_hashes[name] = hashlib.sha256(payload).hexdigest()
        return name, payload

    def error(path, exc):
        errors.append(dict(path=path.relative_to(root).as_posix(), reason=str(exc)))

    def add(combo, path, kind, **extra):
        name = path.relative_to(root).as_posix()
        combinations.setdefault(combo, []).append(dict(path=name, sha256=file_hashes[name], kind=kind, **extra))

    def fixture_registration(path):
        matrix = next((p for p in path.parents if p.name == 'offline-input-matrix'), None)
        manifest = matrix.parent / 'offline-input-matrix-manifest.json' if matrix else None
        manifest_name = manifest.relative_to(root).as_posix() if manifest else None
        data = parsed.get(manifest_name, {})
        relative = path.relative_to(matrix).as_posix() if matrix else None
        name = path.relative_to(root).as_posix()
        if matrix and data.get('files', {}).get(relative) == file_hashes[name]:
            return dict(path=name, sha256=file_hashes[name],
                        reason='hash_verified_registered_single_file_fixture',
                        manifest=manifest_name, manifest_sha256=file_hashes[manifest_name])
        return None

    def partial(path, payload, missing, registered=False):
        name = path.relative_to(root).as_posix()
        entry = dict(path=name, sha256=file_hashes[name], missing=missing,
                     complete_task=False, registered_single_file_fixture=registered)
        try:
            body = dictionary(payload)
            if path.name == 'blockMeshDict':
                nx, ny, nz = mesh_counts(body)
                entry.update(nx=nx, ny=ny, nz=nz, wall_speed=None,
                             mesh_status='literal', wall_speed_status='unknown_missing_velocity')
            else:
                boundaries = body.get('boundaryField', {})
                moving = uniform_velocity(boundaries, 'north')
                fixed = uniform_velocity(boundaries, 'south')
                if fixed != (0, 0, 0) or moving[1:] != (0, 0) or moving[0] <= 0:
                    raise Unresolved('unrecognized_wall_flow')
                entry.update(wall_speed=float(moving[0]), nx=None, ny=None,
                             wall_speed_status='literal', mesh_status='unknown_missing_mesh')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            entry.update(parameter_status='unresolved', reason=str(exc),
                         wall_speed=None, nx=None, ny=None)
        partial_inputs.append(entry)

    # pathlib does not descend into linked directories. Such a directory could
    # conceal task declarations, so do not call the enumerated scope complete.
    for path in archive.rglob('*'):
        if path.is_symlink() and path.is_dir():
            error(path, Unresolved('symlink_directory_not_traversed'))

    for path in sorted(archive.rglob('*.json')):
        counts['json_files'] += 1
        try:
            name, payload = read(path)
            parsed[name] = json.loads(payload.decode('utf-8'), object_pairs_hook=unique_json,
                                      parse_constant=lambda _: (_ for _ in ()).throw(Unresolved('nonfinite_json')))
            for pointer, obj in json_objects(parsed[name]):
                if 'wall_speed' not in obj:
                    continue
                if obj.get('kind') != 'couette' or not all(key in obj for key in ('nx', 'ny')):
                    raise Unresolved('unresolved_wall_speed_object:' + pointer)
                if type(obj['nx']) is not int or type(obj['ny']) is not int or obj['nx'] <= 0 or obj['ny'] <= 0:
                    raise Unresolved('unresolved_spec_mesh:' + pointer)
                if type(obj['wall_speed']) not in (int, float):
                    raise Unresolved('unresolved_spec_wall_speed:' + pointer)
                speed = Fraction(str(obj['wall_speed']))
                if speed <= 0:
                    raise Unresolved('nonpositive_spec_wall_speed:' + pointer)
                add((speed, obj['nx'], obj['ny']), path, 'json_spec', pointer=pointer)
                counts['json_spec_objects'] += 1
        except (OSError, ValueError, TypeError) as exc:
            error(path, exc)

    for mesh in sorted(archive.rglob('blockMeshDict')):
        counts['mesh_files'] += 1
        velocity = mesh.parent.parent / '0/U'
        try:
            name, mesh_payload = read(mesh)
            if not velocity.is_file():
                counts['unpaired_mesh_files'] += 1
                # Registered matrix controls are explicitly single-file parser
                # fixtures. Verify the archived manifest before excluding them.
                registration = fixture_registration(mesh)
                if registration:
                    exclusions.append(registration)
                    counts['registered_single_file_fixtures'] += 1
                    partial(mesh, mesh_payload, '0/U', registered=True)
                elif topology(dictionary(mesh_payload)) == 'legacy_channel':
                    exclusions.append(dict(path=name, sha256=file_hashes[name],
                                           reason='explicit_legacy_channel_topology_without_velocity'))
                    counts['unpaired_legacy_channel'] += 1
                else:
                    raise Unresolved('missing_velocity_for_reference_mesh')
                continue
            counts['input_pairs'] += 1
            velocity_name, u_payload = read(velocity)
            combo = input_combination(mesh_payload, u_payload)
            if combo is None:
                counts['known_non_couette_pairs'] += 1
                continue
            add(combo, mesh, 'input_mesh', companion=velocity_name,
                companion_sha256=file_hashes[velocity_name])
            add(combo, velocity, 'input_velocity', companion=name,
                companion_sha256=file_hashes[name])
            counts['couette_input_pairs'] += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            error(mesh, exc)

    for velocity in sorted(p for p in archive.rglob('U') if p.parent.name == '0'):
        counts['velocity_files'] += 1
        if (velocity.parent.parent / 'system/blockMeshDict').is_file():
            continue
        counts['unpaired_velocity_files'] += 1
        try:
            name, payload = read(velocity)
            registration = fixture_registration(velocity)
            if not registration:
                raise Unresolved('missing_mesh_for_velocity')
            exclusions.append(registration)
            counts['registered_single_file_fixtures'] += 1
            partial(velocity, payload, 'system/blockMeshDict', registered=True)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            error(velocity, exc)

    rows = []
    for (speed, nx, ny), sources in sorted(combinations.items()):
        # All declared decimal literals reduce exactly; scientific syntax and
        # JSON float serialization share one numerical identity for exclusions.
        decimal_speed = str(Decimal(speed.numerator) / Decimal(speed.denominator))
        rows.append(dict(wall_speed=float(speed), wall_speed_decimal=decimal_speed,
                         nx=nx, ny=ny, sources=sources))
    scanned_files = [dict(path=name, sha256=digest) for name, digest in sorted(file_hashes.items())]
    inventory_digest = hashlib.sha256(json.dumps(scanned_files, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    return dict(schema='simclinic-couette-history-inventory-v1', scan_scope='real-runs',
                methods=['Recursively parse every JSON with duplicate/nonfinite rejection and locate explicit Couette speed/mesh objects.',
                         'Parse every blockMeshDict and companion 0/U as restricted literals; record moving north and stationary south wall declarations.',
                         'Explicit legacy-channel and oblique-uniform declarations are outside Couette scope.',
                         'Exclude orphan parser-matrix files only after their archived single-file manifest hash matches; never interpret a fixture directive.',
                         'Any unreadable, duplicate, dynamic, malformed or unclassified target source makes scan_complete false.'],
                scan_complete=not errors, classification_complete_for_full_task_catalog=not errors,
                errors=errors, exclusions=exclusions, partial_inputs=partial_inputs,
                limitations=['Inventory records declarations, including negative controls and rejected inputs; it does not imply successful CFD execution.',
                             'Couette novelty is relative to this local real-runs archive, not external runs, unreadable files or future additions.',
                             'Leading-plus numeric literals are decoded for inventory identity only; this does not claim Foundation 10 accepts them.',
                             'Registered isolated parser fixtures have no full task identity and are excluded explicitly; their partial parameters and unresolved expressions remain listed.',
                             'scan_complete applies to enumerated JSON and the full task/input-pair catalog; it does not turn missing isolated-fixture parameters into known values.'],
                counts=dict(counts), combinations=rows, scanned_files=scanned_files,
                scanned_files_sha256=inventory_digest, new_model_calls=0, new_simulations=0)


def new_output(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Output must be a new file')
    archive = root / 'real-runs'
    if out == archive or archive in out.parents:
        raise ValueError('Historical real-runs tree is read-only')
    for parent in out.parents:
        if (parent / 'freeze.json').is_file() or (parent / 'freeze.sha256').is_file():
            raise ValueError('Frozen tree is read-only')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Repository root containing real-runs')
    parser.add_argument('--out', type=Path, required=True, help='New JSON file outside historical/frozen trees')
    args = parser.parse_args(argv)
    try:
        out = new_output(args.root, args.out)
        report = inventory(args.root)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps(dict(scan_complete=report['scan_complete'], counts=report['counts'],
                              combinations=len(report['combinations']), errors=len(report['errors']),
                              output=str(out)), ensure_ascii=False))
        return 0 if report['scan_complete'] else 2
    except (OSError, ValueError) as exc:
        print('inventory-couette-tasks: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
