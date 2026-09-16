# Fallback counters must separate DISABLED from MISSING REFERENCE

CLAUDE.md's "Fallback transparency (no silent fallbacks)" rule is MANDATORY: every
code path with a fallback counts and logs how many items used the PRIMARY method vs
the FALLBACK, and a high fallback rate is treated as a failure signal. This note adds
the part that rule does not state and that has now cost real review time.

## The rule

A feature that can be switched off has **three** states, not two. Count them apart:

| state | meaning | counted as |
|---|---|---|
| **primary** | the real method ran | primary |
| **missing reference** | the method was applicable but its input was absent for this item | **fallback** |
| **not applicable** | the flag is off, or this item structurally cannot use the method | **neither** |

Folding "not applicable" into the fallback counter makes a deliberate rollback report
as a broken reference. Concretely (issue #317, caught in PR review): with
`fleet_gemeinde_bev_composition_tilt=false` the composition was never looked up — as
intended — but every draw still incremented the fallback counter, so the run log read

```
powertrain Gemeinde BEV/PHEV composition tilt: primary 0/684762 (0.0%), fallback 684762 (100.0%)
```

as a **warning**, because the rate crossed the 50 % threshold. That is the same class
of defect ADR-0086 exists about: a counter that reports a deliberate state as a
failure is as useless as one that reports a failure as success. Both destroy the
signal the no-silent-fallback rule is supposed to provide.

## Disabled must still be VISIBLE

Do not fix this by simply counting nothing and logging nothing. Silence is
indistinguishable from "the feature ran and happened to do nothing" — which is
exactly the state ADR-0086 found (a tilt that was 100 % inert while the log looked
healthy). Log the disabled state explicitly instead:

```
powertrain Gemeinde BEV/PHEV composition tilt: DISABLED
(fleet_gemeinde_bev_composition_tilt=false); both electric powertrains keep the
single combined factor (ADR-0086 behaviour).
```

## How to apply

When adding or reviewing a flag-gated feature that has a fallback:

1. Decide applicability **outside** the flag check, so the counters can tell the three
   states apart. Gating the applicability test on the flag is what merges them.
2. Increment the fallback counter only for *applicable but unavailable*.
3. Log an explicit disabled line when the flag is off.
4. **Test the OFF path in the instrumentation, not only in the output.** An OFF-path
   test that asserts byte-identical numbers passes happily while the counters lie —
   that is precisely how #317 shipped the defect into review. Assert the counters and
   assert that no warning is emitted.

Related: CLAUDE.md "Fallback transparency"; ADR-0086 (a fallback counter that treated
"matched but did nothing" as a primary hit); ADR-0124 decision 8 and its OFF-path
tests in `tests/test_fleet_gemeinde_bev_composition.py`.
