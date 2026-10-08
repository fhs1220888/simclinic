# Optional research archives

The lightweight GitHub checkout contains the diagnostic CLI, synthetic examples,
source tests, fixed third-party integration snapshots, candidate patches and
protocol definitions. Full CFD/model runs and local environments are kept outside
the source repository.

Run the portable checks from the checkout root:

```bash
python3 -m unittest discover -s tests -v
python3 -m simclinic demo --out demo-output/my-demo
```

Tests that require an unavailable historical archive report an explicit skip.
Available but incomplete or changed archives are not converted into skips: their
original integrity and behavioral assertions still run and can fail. A portable
test run must report passed and skipped counts separately; it is not a rerun of
the historical full suite or a new CFD/model evaluation.

## Archive-dependent operations

The 15-case `benchmarks/diagnostics-v1` manifest includes real historical records.
The public synthetic benchmark is a separate six-case manifest at
`benchmarks/synthetic-v1`. Do not replace the research manifest with synthetic
data or describe the synthetic benchmark as real evidence.

Bounded patch delivery verification requires the exact 52-file baseline runtime,
freeze and evidence paths listed in
`patches/simclinic-bounded-sampling-recovery/manifest.json`. Copying only the
candidate patch does not satisfy those requirements.

Execution-freeze construction, physical recomputation and archive replay also
require the archived inputs, logs, model usage, identities and source snapshots
specified by their respective manifests. They should refuse missing data rather
than infer successful completion.

## Restoring evidence

Original evidence is currently retained by the maintainer. No public full-archive
download has been published yet. This checkout does not automatically download
archives or start simulations/model calls to replace missing evidence.

When a reviewed archive distribution becomes available:

1. Obtain its file/hash manifest and provenance from the maintainer.
2. Restore exact bytes and relative paths under `real-runs/`, `execution-freezes/`
   and any explicitly required benchmark data directories.
3. Run the corresponding verification command before interpreting results.

Personal paths in authored retrieval metadata may be made relative in the public
source copy. Such a copy is not an immutable historical execution snapshot.
Historical source trees, primary scores and original evidence remain unchanged.

No public clone is claimed to reproduce all historical results without their
original archives. See [validation scope](validation-scope.md) for measured
results and their limits.
