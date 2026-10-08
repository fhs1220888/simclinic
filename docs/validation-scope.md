# Validation scope

SimClinic diagnoses recorded behavior and evaluates specific candidate policies.
Confirmed observations, cause hypotheses and unmeasured claims stay separate.

## Latest completed observation study

The frozen `bounded-recovery-observation-20261006` study completed four C0
development checks, 48 natural observations and 24 explicit output-loss runs.

| Scenario | Baseline full quality | Candidate full quality |
| --- | --- | --- |
| Natural | 24/24 | 24/24 |
| Explicit first-sample output loss | 0/12 | 12/12 |

There were zero first-sample failures and zero recovery-eligible cases in the
48 natural observations. Natural recovery benefit is **not assessable**.
Injected candidate runs used one additional verified-workspace sampling call,
without a solver rerun; median complete recovery-branch time was 2.808 seconds.

Including development: 1,292 model calls, 17,481,440 usage tokens, 76 solver runs,
88 sampling calls and about 219.3 minutes. Monetary cost is unknown. The batch
had independent archive/evidence verification, retained failures and primary
scores, and preserved its frozen code and quality thresholds.

The effect is limited to the fixed local serial Foam-Agent v2.1.0 workflow,
Foundation 10/simpleFoam and the studied small Couette parameter tasks.
No claim is made of natural recovery benefit, statistical significance, stable
consumption savings, arbitrary-agent compatibility or overall optimization.
C7–C10 are used development data for future work, not fresh held-out evidence.

## Software and physical recomputation

The maintainer's complete archive-equipped workspace passed 363 software tests
and 76/76 archive reviews on local Python 3.9.6 and 3.12.14. Cross-version physical
recomputation identified 647 derived floating-point differences, at most 4 ULP.
Finite equivalence is restricted to declared derived fields; every original
acceptance decision remains identical. The 12 injected baseline failures remain
failures.

These are recorded historical results. The source-only checkout reports its own
actual test and skip counts. Different interpreters, operating systems and
physical mechanisms have not been validated by those historical runs.

The six public fixture cases and synthetic benchmark are authored examples.
They demonstrate diagnostic contracts; they are not executable CFD cases or
proof of physical correctness.

Original evidence remains with the maintainer. Public archive distribution is
pending; [archive requirements](archive-access.md) describe the prerequisites.
