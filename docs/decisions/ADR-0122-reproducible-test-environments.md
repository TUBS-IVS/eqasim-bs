# ADR-0122 · 2026-09-15 · Reproducible test environments and measured consolidation

- **Status:** accepted for implementation

## Context

The maintained branch is `main`, but the inherited regression workflow selected
`develop`, installed JDK 17 and provisioned network extraction tools even for the
small-data test gate. The environment specification named `ile-de-france` while
README instructed users to activate `eqasim`. Its pytest 7.2.2 pin also conflicted
with the pinned chainsolvers commit's pytest >=8.4.2 requirement. A local runtime
could therefore differ from the documented specification without an obvious error.

Test count alone does not measure value or runtime. In the measured Windows
baseline on main, the fleet module's `sampled` and `sampled_v2` fixtures generated
the same 32,000-car input, using seed 42 and the same default-ON path twice.
SimWrapper tests also repeatedly produced identical exports/cards for individual
field assertions. Neither repetition adds an independent input or scenario.

## Decision

1. Use Linux as the canonical scientific execution platform, with WSL2 locally.
   Keep a Windows lock and CI regression leg to exercise native portability.
2. Commit one generated conda-lock environment covering linux-64 and win-64.
   Keep existing direct scientific versions and the chainsolvers commit. Resolve
   the actual pytest conflict with 8.4.2, the minimum required by that commit.
   Install through conda-lock so pip and VCS dependencies are included.
3. Provide `scripts/run_tests.py` as a shared test entry point. It reports the
   actual interpreter/import origins, uses repository-root discovery, enables
   UTF-8 and preserves pytest exit status. Its default selection excludes real
   pipeline runs; `--pipeline` selects those runs through the existing opt-in.
4. Run the regression workflow against main with locked dependencies and retain
   JUnit/duration evidence. Real MATSim runs still need JDK 25 and data/toolchain
   preflight; the regression gate does not install unused extraction tools.
5. Consolidate tests with identical inputs into coherent output-contract checks,
   retaining every assertion and independent edge/override case. Reuse the fleet
   sample once; retain its default-ON, explicit-OFF and determinism coverage.

## Alternatives and consequences

- Arbitrary deletion quotas were rejected: model correctness and scientific
  invariants determine which checks are needed.
- Parameterization alone would shorten code without reducing repeated execution.
- A single environment.yml solve on each host was rejected because it does not
  lock transitive builds. Locks improve reproducibility but do not prove that
  floating-point outputs are bit-identical across operating systems or hardware.
- Dropping Windows support entirely was rejected for this change; existing native
  workflows remain useful and the portability checks expose genuine defects.
- Scientific algorithms, calibration, seed and production parameters are unchanged.
  The production server environment is not automatically replaced by a local setup.

Installation details live in [reproducible-environment](../codebase/notes/reproducible-environment.md).
Commands and test boundaries live in [TESTING](../codebase/TESTING.md). Measured
results are recorded separately under `docs/runs/` rather than inferred from
test counts or successful dependency resolution.
