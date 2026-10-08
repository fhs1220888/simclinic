"""Build patch 0006 in a new snapshot; preserve all previous candidates."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'integration/foam_agent_v210/native/observed'
TARGET = BASE.parent/'content'
PATCH = ROOT/'patches/foam-agent-v2.1.0/0006-file-content-contract.patch'


def main():
    manifest = json.loads((BASE/'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((BASE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Previous snapshot changed: '+name)
    if TARGET.exists() or PATCH.exists():
        raise ValueError('Do not overwrite a built candidate or patch')
    with tempfile.TemporaryDirectory(prefix='simclinic-content-build-') as temporary:
        root = Path(temporary)/'candidate'
        shutil.copytree(BASE, root, ignore=shutil.ignore_patterns('__pycache__'))
        path = root/'src/services/input_writer.py'
        before = path.read_text()
        old = '        code_context = parse_context(generation_response)'
        if before.count(old) != 1:
            raise ValueError('Initial writer contract differs from expected source')
        after = before.replace('from simclinic.agent_paths import case_input_path\n',
            'from simclinic.agent_paths import case_input_path\nfrom simclinic.input_content import decode_foamfile_response\n', 1)
        after = after.replace(old,
            '        code_context = decode_foamfile_response(generation_response, file_name=file_name, folder_name=folder_name)', 1)
        path.write_text(after)
        PATCH.write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
            fromfile='a/src/services/input_writer.py', tofile='b/src/services/input_writer.py')))
        source = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (root/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        updated = dict(base_commit=manifest['base_commit'],
            previous_manifest_sha256=hashlib.sha256((BASE/'candidate-manifest.json').read_bytes()).hexdigest(),
            patch_sha256=hashlib.sha256(PATCH.read_bytes()).hexdigest(), source_files=source,
            runtime_requirements={'simclinic':'0.8.0', 'input_content_sha256':
                hashlib.sha256((ROOT/'simclinic/input_content.py').read_bytes()).hexdigest()})
        (root/'candidate-manifest.json').write_text(json.dumps(updated,indent=2)+'\n')
        shutil.copytree(root, TARGET)
    print('New content candidate:', TARGET)


if __name__ == '__main__':
    main()
