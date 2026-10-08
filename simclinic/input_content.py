"""Decode a single model-produced dictionary without rewriting its parameters.

This validates the response envelope and dictionary header, not OpenFOAM grammar.
The real parser, mesh check and task acceptance remain required after writing.
"""
import json
import re


class InputContentError(ValueError):
    """The response cannot be attributed to an unambiguous file body."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputContentError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


_TOKENS = re.compile(
    r'(?P<comment>/\*.*?\*/|//[^\r\n]*)|(?P<space>\s+)|'
    r'(?P<string>"(?:\\.|[^"\\])*")|(?P<punct>[{};])|'
    r'(?P<word>[^\s{};"]+)|(?P<bad>.)', re.DOTALL)


def _validate_header(body, file_name):
    if not body.strip() or '\x00' in body:
        raise InputContentError('Empty or NUL-containing dictionary')
    tokens = []
    for match in _TOKENS.finditer(body):
        if match.lastgroup in ('comment', 'space'):
            continue
        token = match.group()
        if match.lastgroup == 'bad' or (match.lastgroup == 'word' and re.search(r'\\[nrt]', token)):
            raise InputContentError('Invalid quoting or literal whitespace escape outside a string')
        tokens.append(token)
    if tokens[:2] != ['FoamFile', '{'] or '}' not in tokens:
        raise InputContentError('Expected a FoamFile dictionary header')
    header = tokens[2:tokens.index('}')]
    if '{' in header:
        raise InputContentError('Nested FoamFile header is unsupported')
    objects = [i for i, token in enumerate(header) if token == 'object']
    if len(objects) != 1:
        raise InputContentError('Expected exactly one header object')
    index = objects[0]
    if index + 2 >= len(header) or header[index+2] != ';':
        raise InputContentError('Malformed header object entry')
    value = header[index+1]
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise InputContentError('Invalid quoted header object') from exc
    if value != file_name:
        raise InputContentError('Header object differs from the requested file')


def decode_foamfile_response(response, *, file_name, folder_name):
    """Accept raw body, one complete code fence, or one exact matching JSON file.

    Decode JSON once. Never use unicode_escape, repeated decoding, regex slicing
    from the first FoamFile occurrence, or guessed filename/path corrections.
    Raw dictionaries retain their exact characters, comments and quoted escapes.
    """
    if not isinstance(response, str):
        raise InputContentError('File response must be text')
    body = response
    if response.lstrip().startswith('```'):
        fence = re.fullmatch(
            r'\s*```(?:json|foam|openfoam|cpp|c\+\+|text)?[ \t]*\r?\n(.*?)\r?\n```\s*',
            response, re.DOTALL | re.IGNORECASE)
        if not fence or '```' in fence.group(1):
            raise InputContentError('Expected one complete code fence without surrounding prose')
        body = fence.group(1)
    if body.lstrip().startswith(('{', '[', '"')):
        try:
            envelope = json.loads(body, object_pairs_hook=_unique_object)
        except ValueError as exc:
            raise InputContentError('Invalid or ambiguous JSON file envelope') from exc
        if not isinstance(envelope, dict) or set(envelope) != {'file_name', 'folder_name', 'content'}:
            raise InputContentError('Expected one JSON file with exactly file_name, folder_name and content')
        if envelope['file_name'] != file_name or envelope['folder_name'] != folder_name:
            raise InputContentError('JSON file identity differs from the requested file')
        body = envelope['content']
        if not isinstance(body, str):
            raise InputContentError('JSON file content must be text')
    _validate_header(body, file_name)
    return body
