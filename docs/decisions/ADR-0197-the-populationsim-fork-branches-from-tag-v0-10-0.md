# ADR-0197 · 2026-11-17 · The PopulationSim fork branches from tag v0.10.0

- **Status:** accepted (owner, 2026-11-17, in the WP1 session of issue #138, after reading the
  ladder comparison of arms 0a and 0b).
- **Numbering:** ADR-0197 is the next free id; checked on 2026-11-17 across all local and remote
  branches and the open pull requests.
- **Issue:** #645 (fork core), evidence from #138.

## Context

- The PopulationSim repair (issues #645 to #648) changes PopulationSim itself in the fork
  `TUBS-IVS/populationsim`. The design left open whether the fork branches from the current
  upstream master or from tag v0.10.0, the release production run `prod100_20261031` used. Its
  fallback rule: base on v0.10.0 if upstream master (ladder arm 0b) misses design goal G3 against
  v0.10.0 with the raised solver time limit (arm 0a), or falls back to sequential integerizing more
  often.
- Run manifest [`popsim-ladder-baseline-2026-11-16`](../runs/popsim-ladder-baseline-2026-11-16.yml)
  holds the comparison on `batch_000` and `batch_032`: its G3 result, its fallback counts per
  batch, run times and memory, and the environment findings for the run server.

## Decision

The fork branches from tag **v0.10.0**. Upstream master is not used as the base.

## Rationale

- Arm 0b brings no measurable gain: G3 is met on both batches, and the fallback counts are
  equal over both batches (one more in one batch, one fewer in the other); the numbers are in the
  manifest.
- Arm 0b costs more: a longer run time per batch, and an environment that does not fit the
  pipeline. Its lock file needs a newer `uv` than the shared one, installs a numpy build the run
  server's CPU cannot load for Python 3.11 and later (only the Python 3.10 branch of the lock
  runs), and its checkpoint stores are written with numpy 2, which the pipeline environment
  (numpy 1.x) cannot read.
- The defects the repair targets (integer cast of the seed weights, heaviest-first integerizer
  objective) are present in both versions, so the fork fixes them either way.

## Rejected option

- **Upstream master** (the design's first choice): rejected for the reasons above. It stays
  reachable: an upstream change the fork needs later can be cherry-picked onto the v0.10.0 base.

## Consequences

- WP2 (#645) creates its branch from tag v0.10.0 of the fork and keeps the existing pipeline
  environment; ladder arm 0a (v0.10.0, raised time limit) is the reference for the later arms.
- Later arms that touch the integerizer are compared against arm 0a, not arm 0b.

## Evidence

- Run manifest `popsim-ladder-baseline-2026-11-16` (arms P, 0a, 0b; classification `ab_test`).
