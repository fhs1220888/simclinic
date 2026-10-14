from pathlib import Path
from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent
data = [str(p.relative_to(ROOT / "simclinic")) for p in (ROOT / "simclinic/demo_data").rglob("*") if p.is_file()]

setup(
    name="simclinic",
    version="0.11.0+collect1",
    description="Minimal SimClinic first release with scoped fixed-Agent collection",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    packages=find_packages(exclude=("tests",)),
    package_data={"simclinic": data},
    install_requires=[],
    entry_points={"console_scripts": ["simclinic=simclinic.cli:main", "simclinic-demo=simclinic.showcase:main"]},
    license_files=("NOTICE.md", "LICENSE-Foam-Agent-MIT.txt"),
)
