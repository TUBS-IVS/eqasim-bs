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
2. Capture the known production Linux environment as an explicit conda artifact
   snapshot plus the effective pip layer. Reproduce both layers in WSL2 and Linux
   CI. A newly solved Linux environment was rejected because it selected newer
   transitive packages (including Plotly 7 instead of the server's 6.8). Keep a
   separate Windows conda-lock resolution from environment.yml. Its pytest 9.1.1
   matches the effective server runtime; the Linux snapshot deliberately retains
   the server's conda-then-pip installation history. Keep scientific versions and
   the chainsolvers commit. Verify the effective installation with `pip check`.
3. Provide `scripts/run_tests.py` as a shared test entry point. It reports the
   actual interpreter/import origins, uses repository-root discovery, enables
   UTF-8 and preserves pytest exit status. Its default selection excludes real
   pipeline runs and clears inherited opt-in flags; `--pipeline` selects those
   runs through the existing opt-in, after data/JDK/Maven preflight.
4. Run the regression workflow against main with locked dependencies and retain
   JUnit/duration evidence. Real MATSim runs still need JDK 25 and data/toolchain
   preflight; the regression gate does not install unused extraction tools.
5. Consolidate tests with identical inputs into coherent output-contract checks,
   retaining every assertion and independent edge/override case. Reuse the fleet
   sample and its validation summary once; retain default-ON, explicit-OFF,
   determinism and the raw-KBA-target negative regression coverage.
6. Make historical test fixtures portable: retain native-integer dtype checks
   and fix the historical Windows CSV line ending for its existing golden hash.
   Do not accept additional hashes or disable dtype assertions.
7. Bootstrap new Linux environments from the server snapshot and check the
   effective dependencies. Preserve existing environments; fail an update if
   either snapshot artifact is missing or changes. Use the trusted JDK 25 in
   the Linux pipeline wrapper and reject conflicting configured Java paths.
8. Make the shared runner mandatory in the agent bootstrap, binding rules,
   contributor workflow and PR checklist. Keep the verification process in
   TESTING.md, including final-code evidence and both CI platforms before merge.
   Retire the inherited Travis entry point; retain the explicitly separate
   metadata-only documentation gate.

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
