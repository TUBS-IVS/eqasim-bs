# Commute-day two-view trips (`braunschweig/synthesis/commute_day/`)

What the two views are, which consumer reads which and why, the shim pattern
that lets a vendored eqasim stage read the reporting-day view without being
edited, and the flag-OFF invariant. Why the model exists is ADR-0104
(issue #244); its production state is the Feature Registry record
`commute_day_state`.

## Two views, one population

The pipeline keeps TWO views of the population's day, never one merged view:

| View | Stage names | Fed by | Read by |
|---|---|---|---|
| **Pre-assignment** | `synthesis.population.trips`, `...activities` | the vendored eqasim/popsim trip builder | commute distances, primary candidates, the primary location assignment, `braunschweig.synthesis.commute_day.state_stage` (both the donor's and the worker's distance class) |
| **Reporting-day** (`.final`) | `synthesis.population.trips.final`, `...activities.final` | `braunschweig.synthesis.commute_day.trips_day_stage` / `...activities_day_stage`, splicing a donor day onto every `home` worker and removing every `absent` worker's trips | secondary location choice, the eqasim location join, `synthesis.output`, the MATSim population, the two commute analyses (`work_participation_by_kreis`, `cordon_validation`) |

**Why no cycle.** The commute-day state depends on the ASSIGNED distance class,
which depends on the primary location assignment, which itself depends on the
pre-assignment trips (their distances shape the location choice). If the
reporting-day trips fed the primary assignment, the day state would depend on
its own output through the assignment it conditions on. Keeping the
pre-assignment view untouched and terminal-only for the reporting-day view
breaks that cycle: nothing downstream of the state draw ever feeds back into
anything upstream of it.

**The rule for a new consumer:** a stage that assigns or chooses something
*from* the day (locations, distances, candidate sets) reads the
**pre-assignment** view; a stage that consumes the *finished* day (writes it
out, measures it, hands it to MATSim) reads the **reporting-day** (`.final`)
view. When in doubt, ask which day the consumer needs to see: the one the
person was assigned FROM, or the one the simulation actually runs.

## The shim pattern (`day_view.py`)

Two of the reporting-day stages are not new logic at all — they are the
**vendored eqasim stages run unmodified** against the reporting-day frames,
so the eqasim location-join and output-writer logic can never drift from a
second, re-implemented copy:

- `braunschweig.synthesis.commute_day.spatial_locations_day` runs
  `synthesis.population.spatial.locations.execute` (vendored, unedited).
- `braunschweig.synthesis.commute_day.output_day` runs `synthesis.output.execute`
  (vendored, unedited), additionally merging the drawn `commute_day_state` into
  the persons frame before the vendored writer selects its output columns.

Both are wrapped through two proxies in `day_view.py`:

- `ConfigureDayViewContext` — a `configure()`-time proxy that rewrites
  `synthesis.population.trips` / `...activities` to their `.final` aliases as
  the vendored `configure()` itself declares them, and forwards everything else
  (`config()`, any other attribute) verbatim. The override therefore declares
  EXACTLY the stages the vendored module reads, substituted, and stays in sync
  automatically if a future eqasim version adds a dependency — nothing here
  hand-lists the vendored stage's own dependency set.
- `StageOverrideContext` — an `execute()`-time proxy that answers a FIXED map
  of stage names from frames the override already holds (e.g.
  `"synthesis.population.activities" -> day_activities`) and delegates every
  other name — and every other context method — to the real synpp context,
  which still refuses anything `configure()` did not declare. A name absent
  from the override map is never silently answered with `None`.

`activities_day_stage.py` uses the same idea through its own minimal
`_ActivitiesShimContext` rather than `day_view.py`'s proxies, because it wraps
only ONE vendored call (`synthesis.population.activities.execute`) and that
call mutates the trips frame it is handed in place (adds `trip_count`,
`purpose`, `start_time`, ... columns) — the shim therefore hands it a **copy**
of the reporting-day trips, never the cached original, or every other
consumer of that cached stage output would see the mutation too. Any stage
name the vendored module requests that the shim does not recognise raises a
`KeyError` naming it, rather than returning `None` — a future eqasim
dependency must be noticed here, not silently ignored.

**Rule for touching this shim layer:** never re-implement a vendored eqasim
stage's logic to make it read the reporting-day view. Wrap it through a proxy
instead. If a future eqasim version reads a THIRD stage name, the shim raises
loudly (`KeyError` / `ConfigureDayViewContext`'s pass-through) rather than
silently answering it — extend the shim's stage map, do not patch the
vendored module.

## The flag-OFF invariant

`commute_day_state_enabled` defaults `true` in `configs/base_bs.yml`. With it
`false`:

- `home_office_donors_stage` reads no raw MiD file at all and returns two
  EMPTY frames carrying the ON-path columns (so a downstream consumer sees an
  identical schema either way) plus `{"enabled": False}`.
- `state_stage` marks every worker `at_workplace`, `reason="disabled"`.
- `trips_day_stage` returns the pre-assignment trips frame **by identity**
  (the same object, not a copy) — the `.final` alias is then byte-identical
  by construction, not merely by equal values
  (`tests/test_commute_day_stages.py::test_trips_day_stage_off_returns_the_identical_object`).
- `activities_day_stage` therefore also reproduces the pre-assignment
  activities exactly, since its only input is the (unchanged) `.final` trips.
- `spatial_locations_day` / `output_day` run the vendored logic against those
  unchanged `.final` frames, so their output is byte-identical to the vendored
  stage's own; `output_day` additionally never emits a `commute_day_state`
  column, so `select_person_output_columns` returns the legacy list and
  `persons.csv` keeps its pre-model column set exactly.
- Four consumers -- `output_day`, `matsim.scenario.population`,
  `braunschweig.analysis.synthesis.work_participation_by_kreis` and
  `braunschweig.analysis.cordon_validation` -- gate their OWN dependency on
  `state_stage` at `configure()` time (`context.stage(STATE_STAGE)` is called
  only when `context.config(KEY_ENABLED)` is true), so a workflow running the
  model off never asks `state_stage` for its output through these four paths.

**This does NOT remove `home_office_donors_stage` / `state_stage` /
`trips_day_stage` / `activities_day_stage` from the DAG.** Under
`configs/base_bs.yml` the alias table wires
`synthesis.population.trips.final -> trips_day_stage` (and the sibling
aliases) unconditionally, at the CONFIG level, independent of the runtime
flag value; and `trips_day_stage.configure()` itself declares BOTH
`state_stage` and `home_office_donors_stage` as dependencies unconditionally
(it does not gate on `context.config(KEY_ENABLED)` the way the four consumers
above do). So flipping `commute_day_state_enabled` to `false` while still
running `configs/base_bs.yml` keeps every commute-day stage node in the DAG —
they simply produce their OFF-path output (empty frames, `at_workplace`,
pass-through), never removed nodes. The only way these stages leave the DAG
entirely is to not wire the alias table at all, which is what the
`popsim_open` and `simple_ipf_open` **fixture configs** do: neither
`configs/fixtures/config_popsim_open_braunschweig.yml` nor
`configs/fixtures/config_local_braunschweig_25pct.yml` aliases
`synthesis.population.trips.final` / `...activities.final` /
`synthesis.population.spatial.locations` / `synthesis.output` to any
commute-day module at all (they keep the vendored eqasim stages directly), so
their committed synpp DAG snapshots
(`docs/registry/dag/{popsim_open,simple_ipf_open}.json`) contain zero
`commute_day` stage nodes — checked directly against the committed DAG, not
inferred from the flag alone. Both fixtures additionally set
`commute_day_state_enabled: false` for good measure, but that setting is
redundant there: the DAG wiring, not the flag, is what keeps those two
pipelines out of the model entirely.

## Helper-hash tokens (cache invalidation across the pure/stage split)

Every rule lives in a **pure module** (`donor_pool.py`, `state.py`,
`matching.py`, `plan_replacement.py`) that a synpp stage module wraps for I/O
and plumbing only. synpp hashes only the STAGE module's own source for its
cache key, so an edit to a pure helper would otherwise leave a stale cached
stage output in place while the rule that produced it had changed. Every
stage in this package therefore defines its own `validate(context)` that folds
an `md5` of its pure helpers' `inspect.getsource(...)` into the cache token
(the same mechanism `braunschweig.synthesis.locations.secondary_chainsolvers.validate`
uses):

| Stage | Helper modules hashed |
|---|---|
| `home_office_donors_stage` | `donor_pool`, `braunschweig.popsim.trips`, `braunschweig.popsim.plan_validation` |
| `state_stage` | `state`, `matching` |
| `trips_day_stage` | `plan_replacement` |
| `spatial_locations_day` | `day_view`, the vendored `synthesis.population.spatial.locations` |
| `output_day` | `day_view`, the vendored `synthesis.output` |

`activities_day_stage` is the one exception: it defines no `validate()` of
its own. It holds no pure helper module — every rule it depends on either
lives in the vendored `synthesis.population.activities` (governed by synpp's
default per-file hash of that module) or in `synthesis.population.trips.final`
(governed by ITS OWN `validate()`), so there is nothing left for a helper-hash
token to cover here.

**Rule for touching a pure helper module:** a comment-only or docstring-only
edit still changes `inspect.getsource(...)`'s output and therefore still
devalidates every stage that hashes it. This is deliberate (the whole point
of the token), not a bug to work around — but it means a large helper-module
docstring rewrite carries a real re-solve cost at 100% scale (ADR-0104
records the order of magnitude as hours for the downstream chainsolver
re-solve alone), so batch such edits rather than landing them one comment at
a time.
