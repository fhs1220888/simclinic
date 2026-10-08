# SimClinic

**[English](README.md)** | [简体中文](README.zh-CN.md)

Evidence-based diagnostics and candidate improvements for simulation agents.

**Development version: 0.11.0.** Python CLI + repository-local Skill.
The first integration targets Foam-Agent v2.1.0 and OpenFOAM Foundation 10 /
simpleFoam: small steady, incompressible, single-phase cases.

SimClinic-authored code is [MIT licensed](LICENSE). Bundled third-party source
retains its original licenses and attribution; see [notices](THIRD_PARTY_NOTICES.md).

## Quick start — no model, Docker or network required

From a source checkout, using Python 3.9 or later:

```bash
python3 -m simclinic demo --out demo-output/my-demo
python3 -m simclinic audit fixtures/stale/record.json --case fixtures/stale/case --out demo-output/my-stale-audit
python3 -m simclinic benchmark benchmarks/synthetic-v1/manifest.json --root . --out demo-output/my-benchmark
```

Reports contain Markdown and JSON, event/file evidence, and candidate rules.
All six public examples are explicitly **synthetic**; they do not run CFD.
Use new output directories for each demo or benchmark.

Install the CLI in an isolated environment if desired:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/simclinic --help
```

The diagnostic runtime uses only the standard library. Building an installation
requires setuptools >=61. Installed `demo` users should pass
`--fixtures /path/to/checkout/fixtures`; the wheel does not bundle these examples.

## What it checks

| Check | Evidence used |
| --- | --- |
| Missing preflight | Required checks and handling before each solve |
| Success after unresolved errors | Tool outcomes, exit states, explicit retry lineage and claims |
| Ineffective retries | Error category, input/state fingerprints and arguments |
| Stale results | Caller run/input identity versus producer identity and content hash |
| Missing completion evidence | Task-defined execution, numerical and reference criteria |

Results are `finding`, `pass`, `insufficient_evidence` or `not_applicable`.
Missing logs do not establish agent misconduct. Unknown metrics stay null.
Candidate explanations remain hypotheses unless supported by confirmed evidence.

## Import a recorded run

```bash
python3 -m simclinic import fixtures/healthy/record.json --out demo-output/imported.json
python3 -m simclinic audit demo-output/imported.json --case fixtures/healthy/case --out demo-output/imported-audit
```

See the [record contract](docs/record-format.md).
Normalized JSON is supported; free-text Foam-Agent import remains experimental.
Imported commands are not executed.

## Verified scope and limits

The maintainer completed a frozen 76-run study with independent verification:
natural full quality was 24/24 in both arms; explicit first-sample output-loss
full quality was baseline 0/12 versus candidate 12/12. The candidate used one
extra sampling call without rerunning the solver. Natural failures and recovery
eligibility were 0/48, so **natural recovery benefit is not assessable**.

These are historical observations of a fixed local workflow. They do not prove
arbitrary-agent compatibility, statistical significance, stable savings or
comprehensive optimization. [Validation scope](docs/validation-scope.md)
describes consumption, software checks and cross-version recomputation.

## Tests and optional evidence

```bash
python3 -m unittest discover -s tests -v
```

Portable tests use authored fixtures and mocked processes; Git is needed for
patch round-trip tests. No model or CFD execution is required. Historical archive
checks explicitly skip when their required evidence is absent. Passed and skipped
counts are reported separately. CI runs this source-only suite on Python 3.9 and
3.12.

The full research archives, virtual environments and generated outputs are
excluded from this repository. Public distribution of complete evidence is still
pending. [Archive requirements](docs/archive-access.md) explain how original
records are restored for replay, the 15-case research benchmark and patch checks.

## Candidate integration

- [Foam-Agent patches](patches/foam-agent-v2.1.0/README.md)
- [Sampling gate](patches/simclinic-sampling-gate/README.md)
- [Explicit bounded recovery](patches/simclinic-bounded-sampling-recovery/README.md)
- [SimClinic Skill](skills/simclinic/SKILL.md), available in repository context

The bounded entrypoint is opt-in. It retries only a confirmed missing-output
sample once, rechecks current identities and uses a fresh trusted workspace.
Original failures, extra costs and second failures remain recorded. It does not
change physics, numerics, acceptance thresholds or rerun a solver.

Actual Agent/CFD execution requires separate native dependencies, the fixed
container and model access. Historical scripts and protocol snapshots require
matching archives; they are not a promise of a portable one-command CFD setup.

## Development

See [contributing](CONTRIBUTING.md), [roadmap](docs/public-roadmap.md) and
[third-party notices](THIRD_PARTY_NOTICES.md). Web UI, MCP services, arbitrary
Agent adapters, HPC/concurrent execution and automatic solver tuning remain
outside the current implementation.
