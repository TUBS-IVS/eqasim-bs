# ADR-0134 · 2026-09-25 · Tests are sized by the defect they catch, and built cheap

- **Status:** accepted (maintainer request, 2026-09-25)
- **Numbering:** ADR-0134 is the next free id. Checked on 2026-09-25 across all local and remote
  branches: the highest record anywhere is ADR-0133 on `main`.
- **Issue:** #434

## Context

- The regression suite had 528 test files, 5,926 test functions and 6,441 collected cases
  (about 126,000 lines, 79,000 of them code, against about 105,000 lines of production code).
  A full run through `scripts/run_tests.py` took 25:45 min on Windows and 18:41 min on the
  WSL mirror of the server environment, both platforms running concurrently (baseline in #434).
- The time was concentrated, not spread: 124 of 6,441 cases took 76 % of the test time and
  the fleet tests about half of it. The causes were structural, not the number of tests:
  the `FleetSampler` was rebuilt from disk in 30 test functions, the fleet stage executed
  once per test on a five-person scenario, the whole-repository helper-hash report was
  built twice, every registry record was re-parsed 17 times, two modules drew the identical
  32,000-car sample, and several statistical checks drew 20,000-60,000 cars where a few
  thousand keep the same margin.
- The `Tests` section of `CLAUDE.md` asked agents to "prefer small unit tests" for every kind
  of logic. Together with a reflex of one test per change, that produced one test per field,
  per flag and per helper.
- A read-only pilot review of one area (22 files, 257 tests, analysis and reporting) rated
  58 % keep, 26 % consolidate, 4 % rewrite and 12 % delete. It also found tests that pass for
  the wrong reason: a commute-distance override test whose ids sent every person to the
  fallback, a reader test that restated the regex instead of calling the reader, and a
  fallback test that sampled data on which the fallback cannot fire.

## Decision

1. The `Tests` section of `CLAUDE.md` holds the binding rules; `docs/codebase/TESTING.md` and
   `AGENTS.md` link to them. In short: name the realistic defect before writing a test; test
   at the highest useful level; no reflex tests per class, function, flag or column; assert
   behaviour, not implementation; one contract per input, no duplicate coverage; build
   expensive objects once and size statistical samples by a measured margin; always keep
   regression tests, byte-identical OFF paths, primary-path and fallback-rate checks,
   invariants, fail-fast validation, seeded determinism, IO contracts and the guards that
   keep synpp test doubles faithful; a removed test names the test that still catches its
   failure.
2. Expensive fixtures are shared at module or session scope: `tests/conftest.py` provides
   `committed_fleet_sampler` and the two default 32,000-car draws (consistency-v2 and legacy
   path), `tests/fleet_frames.py` the one synthetic car frame; tests receive copies wherever
   they could mutate the shared object.
3. A sample size is reduced only against a measured margin, and the measurement is written
   next to it in the test.
4. Tests that cannot fail are deleted; tests that never reach the path they name are
   rewritten against the real code.

## Rejected alternatives

- **Review all twelve areas with parallel review agents** (estimated 3-4 million tokens):
  stopped after the pilot on cost. The measured concentration of the runtime showed that a
  targeted pass over the slowest files delivers most of the gain.
- **Delete tests until a count or coverage target is met:** contradicts the rules above; the
  suite lost 21 test functions net, all of them unable to fail or skipped on every machine.
- **Shrink the statistical acceptance samples below what their bands need** (motorhome owner
  age, per-district BEV share against KBA FZ 27.15): kept at the size their tolerance
  requires; reduced only where the margin was measured.

## Consequences

- Wall time with both platforms running concurrently: Windows 25:45 -> 6:53 min (6,430 passed,
  41 skipped, 0 failed), Linux 18:41 -> 7:17 min; the 25 Linux failures are the same
  environment-only set as in the baseline. The later tree also carries 50 new cases merged
  from `main`.
- The fleet modules share one draw through the session fixtures. A test that needs a fresh
  sampler or its own sample (timing, laziness and caching tests) builds it explicitly.
- A new test that takes more than a few seconds states in a comment why it cannot be cheaper.
