# eqasim-bs — model overview

The 10-minute scientific mental model. Live state is generated, not asserted
here: [`generated/STATUS.md`](generated/STATUS.md) (what is active),
[`generated/PIPELINE.md`](generated/PIPELINE.md) (the actual DAG),
[`generated/LINEAGE.md`](generated/LINEAGE.md) (what came from where).

## Purpose and scope

eqasim-bs builds an agent-based transport demand model for the **Zweckverband
Großraum Braunschweig** (ZGB-8, Niedersachsen; 8 Kreise, ~1.13 M inhabitants):
a synthetic population with daily activity chains, assigned to real locations,
exported as a MATSim scenario and simulated with the project's own
`eqasim-java-bs` fork. It is research software — correctness, reproducibility
and traceability outrank convenience; every reference value must trace to a
committed source, and convergence is never called validation.

## Where it came from

The project forked `eqasim-org/eqasim-bavaria` at `b20fbe6` (2025-10-06,
ADR-0000) and inherited the whole eqasim machinery: the synpp pipeline
(population synthesis seeded by the French ENTD 2008 travel survey), the
location-assignment stages, and the Java MATSim layer. Everything since is a
traceable delta on that baseline (`UPSTREAM_DELTA.md`): region-neutral code was
extracted into `eqasim_common/` (ADR-0001), Bavaria's data loaders were
replaced by Niedersachsen equivalents, and the model was extended
feature-by-feature — each flag-gated with a byte-identical OFF path, each
recorded as an ADR. Bug fixes from the actively developed `eqasim-france` are
swept periodically (`UPSTREAM_FIX_SWEEP.md`).

The mechanism of regional override is the **config alias table**: upstream
stage names stay in the DAG, but the run config substitutes Braunschweig
implementations (e.g. `data.census.filtered` → `braunschweig.popsim.stage`).
The Stage Registry records exactly which of the ~112 stages are inherited,
configured, extended, overridden, or new.

## The model, layer by layer

1. **Population synthesis** — one config switch
   (`braunschweig.population.method`) selects among three workflows that fill
   identical stage contracts. Production uses **`popsim_mid`**: PopulationSim
   expands complete **MiD 2023** donor households against Zensus-2022 grid
   controls (100 m cells nested in 1 km batches, prepared by the separate
   `cleancensus` repository), plus Kreis-level controls (employment, education
   degrees, SrV-anchored trip-participation shares, economic status, car/bike
   ownership). Every synthetic person carries a real German donor's attributes
   **and travel-day chain**. `popsim_open` is the open-data twin (ENTD seed);
   `simple_ipf_open` is the legacy in-house IPF.
2. **Attributes** — under `popsim_mid` most person/household attributes are
   donor-carried; income geography comes from signature-preserving donor
   reallocation against INKAR Kreis levels (`placement_income`, ADR-0069).
   A legacy enrichment stage (economic status, licence/PT raking, income
   distributions) exists but **only executes under `simple_ipf_open`** — that
   discovery is issue #255 and is encoded in the Feature Registry.
3. **Vehicle fleet** — per-household fleets sampled from the KBA registry
   (segment/brand mix, HSN/TSN engine attributes, per-Gemeinde BEV tilt),
   consistency-reconciled; plans of car-less households are re-moded.
4. **Locations** — homes are placed cell-accurately into typed ALKIS buildings
   (LoD2 volumes); work/education follow gravity models calibrated on BA
   Pendleratlas flows (per-RegioStaR-7 slopes, VerBindungen sub-Kreis anchor)
   with building-level activity potentials distributing zone totals onto real
   buildings; education uses LSN school/Kita/university registers; secondary
   activities are solved per chain (carla) with MiD purpose-resolved distance
   distributions, SrV-grounded location types (leisure/other) and a dedicated
   escort purpose anchored at children's schools.
5. **External demand** — cordon in-commuters from BA Pendleratlas enter
   through derived road gates and rail stations with Mikrozensus-balanced
   modes; university student in-commuters from LSN enrollment; long-haul
   freight from the open german-wide-freight v3 model crosses the region as a
   fixed-mode `truck` subpopulation (explicitly NOT count-calibrated).
6. **MATSim** — the scenario (network from OSM, schedule from GTFS, urban
   parking inside the BS ring) runs on the eqasim-java-bs 2.2.0 stack.
   **Mode choice is OFF everywhere** — no calibrated modal split exists yet,
   so runs assign routes/schedules under fixed modes.
7. **Analysis & validation** — population control fit (vs the committed
   cleancensus/Kreis/SrV controls), MiD validation report, VerBindungen OD
   validation, cordon gate reports, SimWrapper dashboards. What has actually
   been compared to what, and in which run, is recorded per run in
   [`docs/runs/`](runs/) and per feature in the Feature Registry.

## Current production configuration

`configs/base_bs.yml` (all feature flags, exactly once) composed with a
per-scale overlay (`configs/overlays/test_100pct.yml` is the production
target; 25 % is the currently validated scale). The resolved config — not any
document — is the truth about what is switched on; `docs/generated/STATUS.md`
derives its production column from it.

## Reading deeper

- Feature evidence and applicability per workflow: `registry/features/` +
  [`generated/FEATURES.md`](generated/FEATURES.md)
- Stage semantics and lineage: `registry/stages/` +
  [`generated/STAGES.md`](generated/STAGES.md)
- Every dataset with license, path, downloader: `registry/data/` +
  [`generated/DATA.md`](generated/DATA.md)
- Decisions incl. rejected approaches: `decisions/` +
  [`generated/DECISIONS.md`](generated/DECISIONS.md)
- Scientific method per feature: [`features/`](features/)
