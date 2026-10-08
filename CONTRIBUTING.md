# Contributing

Use Python >=3.9 and run `python3 -m unittest discover -s tests -v`.
Git is required for source patch round-trip checks. Synthetic demos and the
public six-case benchmark require neither model access nor a solver.

Please provide a concrete trigger, expected behavior and evidence. Do not submit
credentials, personal paths, full local environments or unreviewed model logs.
New test fixtures must declare whether they are synthetic, injected or real.
Missing evidence stays unknown; cause hypotheses remain separate from findings.

Keep task quality thresholds and physical requirements explicit. A failed task,
missing evidence or rejected stale output is not full task success. Preserve
original failed attempts and account for all measured consumption.

Full historical archive tests are optional in a source checkout. Restore the
exact evidence before running them; never replace absent real records with a
synthetic success. See [archive access](docs/archive-access.md).

The project is an early development tool. A change with passing unit tests does
not automatically establish CFD correctness or optimization benefit. Real
model/solver evaluation needs its own predeclared tasks, budget and stop rules.
