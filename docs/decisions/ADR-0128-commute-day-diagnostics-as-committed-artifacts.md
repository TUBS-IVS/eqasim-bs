# ADR-0128 · 2026-09-17 · Commute-day diagnostics as committed artifacts, not log excerpts

- **Status:** active
- **Numbering:** ADR-0128 is the next free id. Checked on 2026-09-17 across ALL local and remote
  branches (`git ls-tree docs/decisions/` for every ref, not only `main`): `main` holds up to
  ADR-0125, and two unmerged local branches hold ADR-0126
  (`feature/resource-adaptive-config`) and ADR-0127 (`feature/i409-measure-surrogate-adults`).
  Ids are append-only, so a colliding draft on a sibling branch is renumbered here rather than
  the other way round (the mechanism ADR-0099 records for the ADR-0098 collision).
- **Issue:** #378 (follow-up of #244)
- **Supersedes / amends:** none. Closes three of the six follow-ups ADR-0104 left open.

## Context

ADR-0104 pre-registered six checks for the commute-day-state model. Check 4 asks for four
donor-matching diagnostics: the coarsening rate per step, the not-replaceable share, the
missing-donor-distance share, and **the pool size per matching cell**. After the 100 % proof run
of 2026-09-06 (`docs/runs/commute-day-state-phase-b-proof-100pct-2026-09-06-rerun.yml`) three of
the four had verdicts and the fourth was recorded as `unknown`, for a concrete reason: the
matching loop computes each candidate cell's size in order to decide whether it is large enough,
and then discards it; only the LEVEL a person matched at survived into the diagnostics.

Two further gaps were recorded in the same manifest:

1. The reporting-day trips counters that ruling R9 turned into the decisive evidence
   (`n_donors_immobile` 1,435, `n_donors_without_trips` 0, `n_persons_replaced`,
   `n_trips_removed` / `n_trips_added`) had to be quoted out of `run_log_excerpts.txt`, because
   `braunschweig.synthesis.commute_day.trips_day_stage` **logs** its diagnostics dict and writes
   no structured artefact.
2. `state_diagnostics.json`, the artefact every one of those proofs cites, had **no producer in
   the repository at all** — each copy was built by a snippet typed on the run server.

A diagnostic that exists only as a log line is not evidence a later reader can re-check, and a
snippet that exists only in a shell history is not a reproducible step. Both are at odds with
the project's traceability rule, and both were carried as open follow-ups on the feature record.

## Decision

**1. The pool size per cell is measured as TWO quantities, not one.**

- `donor_pool_size_by_hard_cell` — a census of the donor pool: the number of donors per
  `distance_class` × `has_active_escort` × `has_children_u14` × `has_car` cell, i.e. per cell the
  coarsening cascade can never leave. At most `(len(COMMUTE_CLASS_LABELS) + 1) × 2 × 2 × 2`
  records, small enough to serialise whole.
- `matched_cell_size_by_level` — the realised draw: for every matched person, how many donors
  were actually eligible at the level they matched at, summarised per level
  (`min`/`p25`/`median`/`p75`/`max`, plus `n_persons_single_donor_cell`), with the headline
  `n_persons_matched_from_single_donor_cell` beside it.

Neither is derivable from the other, which is why both are kept. The census cannot see the soft
criteria still in force at a person's level, nor the ruling-R7 education-anchor exclusion, which
narrow that person's cell further; the realised sizes cannot see a cell no person ever reached.
The realised size is recorded inside the loop from the mask that actually selected the donor —
recomputing it afterwards would silently drop the R7 exclusion.

A cell of exactly one donor is the sparsity signal the check is asking after: that donor's day
was the only day on offer, so the draw had no freedom at all. It is counted and logged.

The donor-pool builder already emits a `cells` diagnostic, and it is deliberately NOT reused:
it is keyed on three dimensions only (`distance_class`, `has_children_u14`,
`has_active_escort`), so it merges cells that the `has_car` hard criterion splits, and it counts
the whole pool including the `has_car`-unknown donors that can never be matched to anybody. It
describes the pool as BUILT; check 4 asks about the pool as MATCHED AGAINST. Both are kept, each
under its own stage's diagnostics, and the distinction is stated in both docstrings.

**2. The reporting-day trips diagnostics travel via `context.set_info`, not via the return value.**

`trips_day_stage` is aliased to `synthesis.population.trips.final`, so its return value is the
reporting-day trips frame itself. It reports its diagnostics under one namespaced key
(`INFO_KEY = "commute_day_trips_diagnostics"`), which synpp persists into the working directory's
`pipeline.json` under that stage's own hash and keeps across runs in which the stage is cached.
The payload is passed through `braunschweig.analysis.json_output.json_safe` first, because synpp
serialises the whole meta with a plain `json.dump(meta, f)` once the stage returns: a numpy scalar
raises `TypeError` there and aborts a 100 % run after all of its work, and a `NaN` passes
(`allow_nan` defaults true) but writes a bare `NaN` literal that strict JSON readers then reject.

**3. `scripts/extract_commute_day_diagnostics.py` produces `state_diagnostics.json`.**

It reads a run's synpp working directory, resolves each stage name to its cache entry
(synpp keys them `"<stage name>__<md5 of config>"`), and writes one strict-JSON file holding the
state-stage diagnostics, the reporting-day trips info and the donor-pool diagnostics, each tagged
with the hash it came from. (The donor-pool block's `cells` entry is tuple-keyed in the stage's
own dict; `json_safe` renders those keys with `str`, so they appear as `"('lt10', True, False)"`
strings rather than failing the write.) **Aggregates only**: both pickles also carry population-sized
per-person frames, and this artefact is meant to be committed beside a run manifest.

**4. `docs/runs/TEMPLATE.yml` is the blank manifest, and the loader skips it by name.**

Every other `*.yml` under `docs/runs/` is an executed run and becomes a row of
`docs/generated/RUNS.md`; a template is not a run, and listing one there would be inventing
history. `registries.load_manifests` therefore skips exactly that file name, and a test in
`tests/test_documentation_registry.py` parses it against the manifest schema anyway, so the
template cannot drift away from the schema it demonstrates. It names the standard artefact set of
a commute-day run, including `commute_day_state_shares.csv` and `state_diagnostics.json`.

## Alternatives considered and rejected

**Return `{"trips": …, "diagnostics": …}` from `trips_day_stage`, with a pass-through stage
serving the `.final` alias.** Semantically the cleanest, and rejected on cost: synpp caches every
stage's output, so the pass-through would store a second copy of a 3.4 M-row frame (100 % scale),
and introducing it would devalidate secondary location choice and everything downstream of it —
hours of recomputation — for a diagnostics change. `set_info` is synpp's own mechanism for
exactly this and is already used in `braunschweig/popsim/`.

**Have `trips_day_stage` write its own JSON into `output_path`.** Rejected: it would put output
writing into a synthesis stage, which the project's architecture rule separates deliberately, and
it would produce a file whose relationship to the cache entry that generated it is untracked.

**Fold `braunschweig.analysis.json_output` into `trips_day_stage`'s `_HELPER_MODULES` cache
token.** Rejected, and the reason is recorded in that module: `json_safe` decides only how the
diagnostics are RENDERED, never one value of the returned frame, so hashing it would devalidate
the stage — and hours of everything downstream — on a pure formatting change. The consequence is
that a cached stage keeps the `pipeline.json` entry it wrote when it last ran, which is the
correct reading of a cached stage anyway.

**Report the realised cell size as a dense dict over all seven levels.** Rejected: a level nobody
matched at has no distribution, and a row of zeros there would dilute the summary and read as a
measurement. The dense per-level COUNT remains `matched_by_level`; the distribution omits empty
levels.

**Emit the full soft-criteria cross-product as the census.** Rejected: `distance_class × sex ×
age_class × household_size_class × has_license` runs to thousands of mostly-empty cells, which is
a table, not a diagnostic. The hard-cell census plus the realised distribution answers the same
question at a size that can be committed and read.

**Write the template as an ordinary `docs/runs/*.yml` and let it appear in RUNS.md.** Rejected
outright: it would put a run that never happened into the authoritative run record.

## Consequences

- Check 4's fourth diagnostic is now **instrumented**; it is NOT yet **measured**. No run has
  executed this code, so the pool sizes of the 2026-09-06 proof remain `unknown` and must not be
  back-filled from anything. The next commute-day run measures them.
- The `matching` diagnostics dict grows by three keys; `state_stage` already folds the matching
  diagnostics into its own output, so they reach `state_diagnostics.json` without further wiring.
- Editing `matching.py` changes `state_stage`'s validation token (it hashes that module's source
  deliberately), so the state draw and everything downstream of it are devalidated once.
  `trips_day_stage` is unaffected by the `set_info` addition on its own account, but is
  devalidated through its dependency on the state stage.
- The realised-cell-size bookkeeping is one integer appended per matched person; on the 5,086
  persons of the 100 % proof run this is not measurable against the O(persons × donors) mask
  arithmetic that surrounds it.
- Three of ADR-0104's six open follow-ups are closed by this record (pool size per cell, a
  committed JSON for the reporting-day trips diagnostics, and the check-1 table as standard
  output). The other three stay open: the 100 % proof inside a scheduled production run, the
  re-specification of check 1, and the 37,705 workers with an assigned workplace whom the
  population does not flag employed.
