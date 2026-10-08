"""Apply pinned candidate patches, then connect native graph postprocessing."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from build_candidate_patches import replace_once

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'integration/foam_agent_v210/native/upstream'
TARGET = BASE.parent / 'patched'
PATCHES = ROOT / 'patches/foam-agent-v2.1.0'


def transform(root):
    def edit(name, changes):
        path = root / name
        text = path.read_text()
        for old, new in changes:
            text = replace_once(text, old, new)
        path.write_text(text)

    # Importing deterministic nodes must not initialize models/RAG or read OAuth.
    edit('src/utils.py', [
        (line, '') for line in (
            'from langchain.chat_models import init_chat_model\n',
            'from langchain_community.vectorstores import FAISS\n',
            'from langchain_openai.embeddings import OpenAIEmbeddings\n',
            'from langchain_aws import ChatBedrock, ChatBedrockConverse\n',
            'from langchain_anthropic import ChatAnthropic\n',
            'import tracking_aws\n',
            'from langchain_ollama import ChatOllama\n',
            'try:\n    from langchain_huggingface import HuggingFaceEmbeddings\nexcept ImportError:\n    HuggingFaceEmbeddings = None\n')
    ] + [
        ('    if provider == "openai":\n        return OpenAIEmbeddings', '    if provider == "openai":\n        from langchain_openai.embeddings import OpenAIEmbeddings\n        return OpenAIEmbeddings'),
        ('    if provider == "huggingface":\n        if HuggingFaceEmbeddings is None:', '    if provider == "huggingface":\n        try:\n            from langchain_huggingface import HuggingFaceEmbeddings\n        except ImportError:\n            HuggingFaceEmbeddings = None\n        if HuggingFaceEmbeddings is None:'),
        ('    embedding_model = get_embedding_model(cfg)', '    from langchain_community.vectorstores import FAISS\n    embedding_model = get_embedding_model(cfg)'),
        ('FAISS_DB_CACHE = load_faiss_dbs()', '# Load RAG only when retrieval is actually requested.\n_FAISS_DB_LOADED = False'),
        ('    if database_name not in FAISS_DB_CACHE:', '    global FAISS_DB_CACHE, _FAISS_DB_LOADED\n    if not _FAISS_DB_LOADED and not FAISS_DB_CACHE:\n        FAISS_DB_CACHE = load_faiss_dbs()\n        _FAISS_DB_LOADED = True\n    if database_name not in FAISS_DB_CACHE:'),
        ('        if self.model_provider.lower() == "bedrock":\n            bedrock_runtime', '        if self.model_provider.lower() == "bedrock":\n            import tracking_aws\n            from langchain_aws import ChatBedrockConverse\n            bedrock_runtime'),
        ('        elif self.model_provider.lower() == "anthropic":\n            self.llm', '        elif self.model_provider.lower() == "anthropic":\n            from langchain_anthropic import ChatAnthropic\n            self.llm'),
        ('        elif self.model_provider.lower() == "openai":\n', '        elif self.model_provider.lower() == "openai":\n            from langchain.chat_models import init_chat_model\n'),
        ('        elif self.model_provider.lower() == "ollama":\n', '        elif self.model_provider.lower() == "ollama":\n            from langchain_ollama import ChatOllama\n'),
        ('    termination_reason: Optional[str]\n', '    termination_reason: Optional[str]\n    simclinic_context: Optional[dict]\n    plot_configs: Optional[list]\n    plot_outputs: Optional[list]\n    visualization_summary: Optional[dict]\n    pyvista_visualization: Optional[dict]\n'),
    ])
    (root / 'src/services/__init__.py').write_text('''"""Delay backend initialization until an actual model call."""
from utils import LLMService
from config import Config


class LazyLLMService:
    def __init__(self):
        self.service = None

    def __getattr__(self, name):
        if self.service is None:
            self.service = LLMService(Config())
        return getattr(self.service, name)


global_llm_service = LazyLLMService()
''')
    edit('src/nodes/local_runner_node.py', [
        ('from logger import log_review', 'from logger import log_review\nfrom simclinic.foam_runtime import begin_run, finish_run'),
        ('    error_logs = run_allrun_and_collect_errors(case_dir, max_time_limit)', '    context = begin_run(case_dir)\n    error_logs = run_allrun_and_collect_errors(case_dir, max_time_limit)\n    error_logs = finish_run(case_dir, context, error_logs)'),
        ('        "error_logs": error_logs', '        "error_logs": error_logs,\n        "simclinic_context": context,\n        "plot_outputs": [], "plot_configs": [],\n        "pyvista_visualization": {}, "visualization_summary": {}'),
    ])
    edit('src/nodes/reviewer_node.py', [
        ('    # Log error logs to review.log', '''    if state.get("loop_count", 0) >= state["config"].max_loop:
        return {**state, "termination_reason": "max_review_loop_reached"}
    # Log error logs to review.log'''),
    ])
    edit('src/nodes/visualization_node.py', [
        ('import os\n', 'import os\nfrom simclinic.foam_runtime import verified_workspace\nfrom simclinic.result_guard import ResultRejected\n'),
        ('def visualization_node(state):', 'def _visualization_in_workspace(state):'),
    ])
    with (root / 'src/nodes/visualization_node.py').open('a') as file:
        file.write('''\n\ndef visualization_node(state):
    """The native node consumes only the current run's verified snapshot."""
    try:
        with verified_workspace(state.get("case_dir"), state.get("simclinic_context")) as work:
            result = _visualization_in_workspace({**state, "case_dir": str(work)})
        result["case_dir"] = state["case_dir"]
        if not result.get("pyvista_visualization", {}).get("success"):
            result["termination_reason"] = "postprocess_failed"
        return result
    except (ResultRejected, OSError, ValueError) as exc:
        error = {"code": getattr(exc, "code", "provenance_error"), "error": str(exc)}
        return {**state, "plot_configs": [], "plot_outputs": [],
                "visualization_summary": error,
                "pyvista_visualization": {"success": False, **error},
                "termination_reason": "postprocess_rejected"}
''')
    edit('src/services/visualization.py', [
        ('from . import global_llm_service', 'from . import global_llm_service\nfrom pathlib import Path\nfrom simclinic.foam_runtime import verify_workspace, record_image\nfrom simclinic.result_guard import ResultRejected'),
        ('    script_path = os.path.join(case_dir, filename)', '''    verify_workspace(case_dir)
    for name in (filename, expected_png):
        if name is None or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ResultRejected("unsafe_output", "Explicit relative script and PNG paths are required.")
    script_path = os.path.join(case_dir, filename)'''),
        ('    try:\n        completed = subprocess.run(', '''    # Every attempt must create its own image; stale PNGs cannot prove success.
    if os.path.lexists(expected_png_abs):
        os.unlink(expected_png_abs)
    try:
        completed = subprocess.run('''),
        ('        if expected_png_abs:\n', '''        Path(case_dir, "render.stdout.log").write_bytes(completed.stdout)
        Path(case_dir, "render.stderr.log").write_bytes(completed.stderr)
        verify_workspace(case_dir)
        if expected_png_abs:
'''),
        ('                return True, expected_png_abs, []', '                record_image(case_dir, expected_png_abs)\n                return True, expected_png_abs, []'),
        ('    except subprocess.TimeoutExpired as e:', '    except ResultRejected:\n        raise\n    except subprocess.TimeoutExpired as e:'),
    ])
    edit('src/router_func.py', [
        ('        return "visualization" if requires_visualization else END', '        # Exhausted solver errors must never proceed to postprocessing.\n        return END'),
    ])
    edit('src/main.py', [
        ('def create_foam_agent_graph() -> StateGraph:', 'def create_foam_agent_graph(entrypoint="planner") -> StateGraph:'),
        ('    workflow.add_edge(START, "planner")', '    if entrypoint not in {"planner", "local_runner"}:\n        raise ValueError("Supported entrypoints: planner or local_runner")\n    workflow.add_edge(START, entrypoint)'),
        ('        else:\n            print("<workflow_end>Workflow completed successfully!</workflow_end>")', '''        elif result.get("error_logs") or termination_reason in {"postprocess_failed", "postprocess_rejected"}:
            print("<workflow_end>Workflow failed; results must not be reported as completed.</workflow_end>")
        else:
            print("<workflow_end>Workflow execution completed; CFD acceptance requires separate verification.</workflow_end>")'''),
    ])
    # Wire the existing tool entry to the same gated node. No new MCP service.
    edit('src/mcp/fastmcp_server.py', [
        ('    log_files: Dict[str, str] = Field(description="Paths to log files")', '''    log_files: Dict[str, str] = Field(description="Paths to log files")
    run_id: str = Field(description="Generation-time execution ID")
    input_version: str = Field(description="Generation-time input hash")'''),
        ('        # Run locally\n        error_logs = run_allrun_and_collect_errors(', '''        from simclinic.foam_runtime import begin_run, finish_run
        context = begin_run(request.case_dir)
        # Run locally
        error_logs = run_allrun_and_collect_errors('''),
        ('        # Convert error logs to strings if they\'re dictionaries', '''        error_logs = finish_run(request.case_dir, context, error_logs)
        # Convert error logs to strings if they're dictionaries'''),
        ('            log_files=log_files\n', '            log_files=log_files,\n            run_id=context["run_id"],\n            input_version=context["input_version"]\n'),
        ('    visualization_type: str = Field(default="pyvista", description="Visualization type")', '''    visualization_type: str = Field(default="pyvista", description="Visualization type")
    expected_run_id: str = Field(description="Current run ID from the execution orchestrator")
    expected_input_version: str = Field(description="Current input hash from the execution orchestrator")'''),
    ])
    mcp_path = root / 'src/mcp/fastmcp_server.py'
    text = mcp_path.read_text()
    start = text.index('        # Ensure foam file exists\n', text.index('async def visualization('))
    end = text.index('        await ctx.info(f"Generated {len(artifacts)} visualization artifact(s)")', start)
    text = text[:start] + '''        from nodes.visualization_node import visualization_node
        state = visualization_node({
            "case_dir": request.case_dir, "user_requirement": request.quantity,
            "config": global_config,
            "simclinic_context": {"run_id": request.expected_run_id,
                                 "input_version": request.expected_input_version},
        })
        if not state.get("pyvista_visualization", {}).get("success"):
            raise ValueError(str(state.get("visualization_summary")))
        artifacts = state["plot_outputs"]
        script = state["pyvista_visualization"]["script"]

''' + text[end:]
    mcp_path.write_text(text)


def main():
    manifest = json.loads((BASE / 'source-manifest.json').read_text())
    for name, sha in manifest['sha256'].items():
        if hashlib.sha256((BASE / name).read_bytes()).hexdigest() != sha:
            raise ValueError('Pinned native source changed: ' + name)
    with tempfile.TemporaryDirectory(prefix='simclinic-native-build-') as tmp:
        root = Path(tmp)
        shutil.copytree(BASE, root, dirs_exist_ok=True)
        for name in ('0001-execution-status.patch', '0002-verified-result-interface.patch'):
            subprocess.run(['git','apply',str(PATCHES / name)],cwd=root,check=True,capture_output=True)
        before = {str(p.relative_to(root)): p.read_text() for p in (root / 'src').rglob('*.py')}
        transform(root)
        diff = []
        for path in sorted((root / 'src').rglob('*.py')):
            relative = str(path.relative_to(root))
            for line in difflib.unified_diff(before[relative].splitlines(True),path.read_text().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative):
                diff.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
        patch = PATCHES / '0003-native-postprocessing.patch'
        patch.write_text(''.join(diff))
        shutil.copytree(root, TARGET, dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    hashes={str(p.relative_to(TARGET)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((TARGET/'src').rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    (TARGET/'candidate-manifest.json').write_text(json.dumps({'base_commit':manifest['commit'],'source_files':hashes,'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest()},indent=2)+'\n')
    print('Native package connected; baseline sources unchanged.')


if __name__ == '__main__':
    main()
