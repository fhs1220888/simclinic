"""Build an opt-in observed candidate without changing the 0.5 snapshots."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from build_candidate_patches import replace_once

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'integration/foam_agent_v210/native/full'
TARGET=BASE.parent/'observed'
PATCH=ROOT/'patches/foam-agent-v2.1.0/0005-observed-decisions-completion.patch'


def transform(root):
    def edit(name,changes):
        path=root/name;content=path.read_text()
        for old,new in changes:content=replace_once(content,old,new)
        path.write_text(content)
    edit('src/utils.py',[
        ('    if os.getenv("SIMCLINIC_DOCKER_IMAGE"):', '''    if os.getenv("SIMCLINIC_OBSERVED_EXECUTION") == "1":
        from simclinic.session_execution import run_container
        return run_container(script_path, out_file, err_file, working_dir, max_time_limit)
    if os.getenv("SIMCLINIC_DOCKER_IMAGE"):'''),
        ('    simclinic_context: Optional[dict]\n','    simclinic_context: Optional[dict]\n    completion_claim: Optional[dict]\n    acceptance_report: Optional[dict]\n'),
    ])
    edit('src/main.py',[
        ('def create_foam_agent_graph(entrypoint="planner", observer=None)',
         'def create_foam_agent_graph(entrypoint="planner", observer=None, completion_policy=None)'),
        ('            observer("end", name, result)', '            observer("end", name, {**state, **result})'),
        ('    # Add nodes\n', '''    def observed_route(name, function):
        def route(state):
            destination = function(state)
            if destination == END and completion_policy is not None:
                destination = "completion"
            if observer is not None:
                observer("route", name, {**state, "route_destination": destination})
            return destination
        return route

    if completion_policy is not None:
        workflow.add_node("completion", observed("completion", completion_policy))
        workflow.add_edge("completion", END)

    # Add nodes
'''),
        ('    workflow.add_edge("visualization", END)',
         '    workflow.add_edge("visualization", "completion" if completion_policy is not None else END)'),
    ]+[(f'workflow.add_conditional_edges("{node}", {router})',
        f'workflow.add_conditional_edges("{node}", observed_route("{router}", {router}))')
       for node,router in [('planner','route_after_planner'),('input_writer','route_after_input_writer'),
                           ('hpc_runner','route_after_runner'),('local_runner','route_after_runner'),('reviewer','route_after_reviewer')]])
    edit('src/services/input_writer.py',[
        ('    # Prepare updated structures\n', '''    if not allowed_files:
        raise ValueError("Rewrite plan must explicitly name files; no unrestricted rewrite.")

    # Prepare updated structures
'''),
        ('        if allowed_files and rel_path not in allowed_files:\n            print(f"Warning: Skipping unplanned rewrite file: {rel_path}")\n            continue',
         '        if rel_path not in allowed_files:\n            raise ValueError("Unplanned rewrite file: " + rel_path)'),
    ])


def main():
    manifest=json.loads((BASE/'candidate-manifest.json').read_text())
    for name,sha in manifest['source_files'].items():
        if hashlib.sha256((BASE/name).read_bytes()).hexdigest()!=sha:raise ValueError('Candidate changed: '+name)
    with tempfile.TemporaryDirectory(prefix='simclinic-observed-build-') as temporary:
        root=Path(temporary);shutil.copytree(BASE,root,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        before={str(p.relative_to(root)):p.read_text() for p in (root/'src').rglob('*.py')}
        transform(root)
        diff=[]
        for path in sorted((root/'src').rglob('*.py')):
            name=str(path.relative_to(root))
            for line in difflib.unified_diff(before[name].splitlines(True),path.read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name):
                diff.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
        PATCH.write_text(''.join(diff))
        shutil.copytree(root,TARGET,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    hashes={str(p.relative_to(TARGET)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (TARGET/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    (TARGET/'candidate-manifest.json').write_text(json.dumps(dict(base_commit=manifest['base_commit'],
        previous_manifest_sha256=hashlib.sha256((BASE/'candidate-manifest.json').read_bytes()).hexdigest(),
        patch_sha256=hashlib.sha256(PATCH.read_bytes()).hexdigest(),source_files=hashes),indent=2)+'\n')
    print('Observed native candidate built:',TARGET)


if __name__=='__main__':main()
