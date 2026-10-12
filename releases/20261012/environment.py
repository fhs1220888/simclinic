"""Write installed-runtime facts; does not run a solver."""
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import simclinic

facts = {
    "python": sys.version, "python_executable": sys.executable,
    "package_version": version("simclinic"), "package_location": simclinic.__file__,
    "platform": platform.platform(), "architecture": platform.machine(),
    "wm_project": os.environ.get("WM_PROJECT"), "wm_project_version": os.environ.get("WM_PROJECT_VERSION"),
    "wm_project_dir": os.environ.get("WM_PROJECT_DIR"), "wm_options": os.environ.get("WM_OPTIONS"),
    "git": subprocess.check_output(["git", "--version"], text=True).strip(),
    "os_release": Path("/etc/os-release").read_text(),
    "installed_system_packages": subprocess.check_output(["dpkg-query", "-W", "openfoam10", "git", "ca-certificates"], text=True),
    "tools": {},
}
for name in ("blockMesh", "checkMesh", "simpleFoam", "git", "bash"):
    path = Path(shutil.which(name)).resolve()
    facts["tools"][name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
if facts["wm_project"] != "OpenFOAM" or facts["wm_project_version"] != "10":
    raise RuntimeError("Source Foundation OpenFOAM10 before running this demo")
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
Path(sys.argv[1]).write_text(json.dumps(facts, indent=2) + "\n")
