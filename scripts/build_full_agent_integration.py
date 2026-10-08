"""Build a separate candidate for actual model-driven native graph runs."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from build_candidate_patches import replace_once

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'integration/foam_agent_v210/native/patched'
TARGET = BASE.parent / 'full'
PATCH = ROOT / 'patches/foam-agent-v2.1.0/0004-full-agent-backends.patch'


def transform(root):
    def edit(name, changes):
        path = root / name
        content = path.read_text()
        for old, new in changes:
            content = replace_once(content, old, new)
        path.write_text(content)

    with (root/'src/services/__init__.py').open('a') as file:
        file.write('''\n\ndef configure_llm(service):
    """Inject a real backend through the shared native service interface."""
    global_llm_service.service = service
''')
    edit('src/utils.py', [
        ('def retrieve_faiss(database_name:', '''_REFERENCE_ADAPTER = None


def configure_retriever(adapter):
    global _REFERENCE_ADAPTER
    _REFERENCE_ADAPTER = adapter


def retrieve_faiss(database_name:'''),
        ('    global FAISS_DB_CACHE, _FAISS_DB_LOADED\n', '''    if _REFERENCE_ADAPTER is not None:
        return _REFERENCE_ADAPTER.search(database_name, query, topk)
    global FAISS_DB_CACHE, _FAISS_DB_LOADED
'''),
        ('    print(f"Executing script {script_path} in {working_dir}")', '''    if os.getenv("SIMCLINIC_DOCKER_IMAGE"):
        from simclinic.foam_execution import run_container
        return run_container(script_path, out_file, err_file, working_dir, max_time_limit)
    print(f"Executing script {script_path} in {working_dir}")'''),
    ])
    edit('src/main.py', [
        ('def initialize_state(user_requirement: str, config: Config, custom_mesh_path: Optional[str] = None)',
         'def initialize_state(user_requirement: str, config: Config, custom_mesh_path: Optional[str] = None, llm_service=None)'),
        ('llm_service=LLMService(config),', 'llm_service=llm_service if llm_service is not None else LLMService(config),'),
        ('def create_foam_agent_graph(entrypoint="planner")', 'def create_foam_agent_graph(entrypoint="planner", observer=None)'),
        ('    # Add nodes\n', '''    def observed(name, function):
        if observer is None:
            return function
        def invoke(state):
            observer("start", name, state)
            try:
                result = function(state)
            except Exception as exc:
                observer("error", name, {"exception_type": type(exc).__name__})
                raise
            observer("end", name, result)
            return result
        return invoke

    # Add nodes
'''),
    ] + [
        (f'workflow.add_node("{name}", {name}_node)', f'workflow.add_node("{name}", observed("{name}", {name}_node))')
        for name in ('planner', 'meshing', 'input_writer', 'local_runner', 'hpc_runner', 'reviewer', 'visualization')
    ])
    edit('src/nodes/planner_node.py', [
        ('        print(f"Warning: Case directory {case_dir} already exists. Overwriting.")\n        shutil.rmtree(case_dir)',
         '        raise ValueError("Use a fresh case directory; existing cases are never overwritten.")'),
    ])
    edit('src/nodes/input_writer_node.py', [
        ("case_solver=state['case_stats']['case_solver'],", "case_solver=state['case_solver'],"),
        ('        mesh_commands=mesh_commands,\n', '        mesh_commands=mesh_commands,\n        user_requirement=state["user_requirement"],\n'),
    ])
    edit('src/nodes/visualization_node.py', [
        ('import os\n', 'import os\nfrom simclinic.rendering_request import choose_field\n'),
        ('''    if not user_requirement:
        return "U"
    text = user_requirement
    # Prefer explicit mentions
    if " p " in f" {text} " or "pressure" in text.lower():
        return "p"
    if "temperature" in text.lower():
        return "T"
    if "u" in text.lower() or "velocity" in text.lower():
        return "U"
    return "U"''', '    return choose_field(user_requirement)'),
        ('max_loop = getattr(state.get("config"), "max_loop", 2)',
         'max_loop = getattr(state.get("config"), "visualization_max_loop", getattr(state.get("config"), "max_loop", 2))'),
    ])
    edit('src/services/input_writer.py', [
        ('import shutil\n', 'import shutil\nfrom simclinic.agent_paths import case_input_path\n'),
        ('    subtasks = sorted(subtasks, key=compute_priority)', '''    # Validate the whole plan before spending calls or creating files.
    for subtask in subtasks:
        case_input_path(case_dir, subtask["folder_name"], subtask["file_name"])
    subtasks = sorted(subtasks, key=compute_priority)'''),
        ('file_path = os.path.join(case_dir, folder_name, file_name)', 'file_path = str(case_input_path(case_dir, folder_name, file_name))'),
        ('file_path = os.path.join(case_dir, foamfile.folder_name, foamfile.file_name)',
         'file_path = str(case_input_path(case_dir, foamfile.folder_name, foamfile.file_name))'),
        ("match = re.search(r'```(.*?)```', text, re.DOTALL)",
         "match = re.search(r'```(?:bash|sh|shell)?[ \\t]*\\n(.*?)```', text, re.DOTALL)"),
    ])
    edit('src/services/plan.py', [
        ('        "Make sure that your output is valid JSON and strictly adheres to the provided schema."',
         '''        "Make sure that your output is valid JSON and strictly adheres to the provided schema."
        "Only include OpenFOAM dictionaries and initial fields in folders 0, constant and system. "
        "Allrun is generated by a separate service; rendering/validation are separate nodes. "
        "Never include Allrun, Python scripts, images, logs, or files in the case root in subtasks."'''),
        ('print(f"Retrieved {len(faiss_structure_all)} candidates from FAISS.")',
         'print(f"Retrieved {len(faiss_structure_all)} candidates from the configured reference adapter.")'),
    ])


def main():
    manifest = json.loads((BASE/'candidate-manifest.json').read_text())
    for name, sha in manifest['source_files'].items():
        if hashlib.sha256((BASE/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Native candidate changed: '+name)
    with tempfile.TemporaryDirectory(prefix='simclinic-full-build-') as directory:
        root = Path(directory)
        shutil.copytree(BASE, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        before = {str(p.relative_to(root)):p.read_text() for p in (root/'src').rglob('*.py')}
        transform(root)
        lines = []
        for path in sorted((root/'src').rglob('*.py')):
            name = str(path.relative_to(root))
            for line in difflib.unified_diff(before[name].splitlines(True),path.read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name):
                lines.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
        PATCH.write_text(''.join(lines))
        shutil.copytree(root, TARGET, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    hashes = {str(p.relative_to(TARGET)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (TARGET/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    (TARGET/'candidate-manifest.json').write_text(json.dumps(dict(base_commit=manifest['base_commit'],
        previous_manifest_sha256=hashlib.sha256((BASE/'candidate-manifest.json').read_bytes()).hexdigest(),
        patch_sha256=hashlib.sha256(PATCH.read_bytes()).hexdigest(),source_files=hashes),indent=2)+'\n')
    print('Full graph candidate built:', TARGET)


if __name__ == '__main__':
    main()
