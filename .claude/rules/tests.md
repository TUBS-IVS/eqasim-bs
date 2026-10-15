---
paths:
  - "tests/**"
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/conftest.py"
  - "**/src/test/**"
  - "pytest.ini"
  - "scripts/run_tests.py"
---

# Test design (path-scoped rule)

Claude Code loads this file when a matching file is read, written or edited (`paths` above);
it is not part of the always-loaded `CLAUDE.md`. The mandatory verification gate (how tests
are RUN) stays in `CLAUDE.md` "Tests"; this file holds the binding rules for how tests are
WRITTEN (ADR-0134; moved out of `CLAUDE.md` by ADR-0143).

## Tests

**A test exists to catch a plausible bug.** Optimise for defect detection per maintained
line and per second of suite time, never for test count or coverage. The suite is already
large (~6,400 cases); every new test must earn its place.

- **Failure modes first.** Before writing a test, name the realistic defect it catches and
  check the test would fail for it. For complex logic, write the expected behaviour and its
  failure cases down before the code. No convincing defect -> no test.
- **Highest useful level.** Prefer one behavioural test on a small synthetic or committed
  input (a stage's `execute()`, a CLI or writer end to end, a round trip) over unit tests.
  Isolate a unit only where that buys real value: non-trivial algorithms and numerics,
  parsers and serialisation, edge-case-rich domain rules, error paths hard to reach from
  above, subtle regressions.
- **No reflex tests.** Never add a test per new class, function, branch, config key or
  column. TDD here means one failing behavioural test that specifies the feature or
  reproduces the bug, not a unit test per helper.
- **Assert behaviour, not implementation.** A test must survive a behaviour-preserving
  refactor: no tests of private helpers whose effect the public function shows, no expected
  values computed by re-implementing the production logic (hand-compute them or assert a
  property), no exact log wording, float formatting or ordering unless that is the contract.
  Assert on source text only for a justified structural constraint (synpp hash / validate
  token coverage, a policy lint).
- **One contract, one test; no duplicates.** For one input, assert the whole output contract
  in one test; use `parametrize` for input variants; never re-test a behaviour that a
  stronger test already covers, in this file or another.
- **Cheap by construction.** Build expensive objects (a `FleetSampler`, a parsed registry,
  a stage run) once per module or session and share them; size a statistical sample by the
  margin it needs, measured, not by habit. A test that takes more than a few seconds must
  say in a comment why it cannot be cheaper.
- **Always keep, and write where missing:** regression tests for observed bugs, the
  byte-identical OFF path of every flag, the primary path and the fallback rate of every
  fallback, conservation and invariants, fail-fast input validation, seeded determinism,
  IO/serialisation contracts, and the guards that keep hand-written synpp doubles faithful.
- **Removing a test** is fine when a stronger test catches the same failure (name it in the
  commit); never remove the only coverage of a behaviour without a stronger replacement, and
  never skip or weaken a test to get green.

Tests are deterministic and use small synthetic data where possible; large external
datasets stay out of unit tests.
