# TAZ-based WORK location choice

## Purpose

The standard eqasim gravity model assigns WORK destinations at Gemeinde
(commune) resolution. For kreisfreie Staedte such as Braunschweig, where a
large share of residents also work within the same commune, this collapses the
intra-city travel-distance distribution to zero: all intra-commune commutes are
treated as zero-distance, which understates intra-city commute distances and
distorts mode-choice utility.

This feature replaces the WORK gravity zone with RVB VISUM Verkehrszellen
(traffic-analysis zones, TAZ) at sub-commune resolution. Within a kreisfreie
Stadt the commune is split into ~30-40 TAZ, giving the gravity model meaningful
intra-city OD distances. The BA Pendleratlas (Kreis-to-Kreis commuter flow)
stays the authoritative inter-Kreis control; the TAZ zones only refine the
within-commune spatial distribution.

## Flag

`taz_work_location_choice` (default: `false`)

- Applies to: WORK destination gravity only. Education and secondary locations
  are not affected.
- When OFF: behaviour is byte-identical to the pre-TAZ pipeline.
- When ON: requires the local-only RVB VISUM parquet (see Data below).
- Carried explicitly (as `false`) in the four real-data popsim configs so the
  flag is not forgotten when the data becomes available.

## Key design decisions

**Potential_work split preserves commune totals.** When TAZ are used, the
aggregate SvB employment for each commune (from the census employment stage) is
distributed across its constituent TAZ in proportion to each TAZ's
`potential_work` (building-area-weighted or uniform fallback). This is a
commune-total-preserving redistribution: summing over TAZ reproduces the
commune total, so the Kreis-level BA commuter-flow calibration is not disturbed.

**Commune-as-TAZ + Kreis-constrained fallback.** Communes with no TAZ
coverage (those outside the RVB VISUM extent) are represented by a single
synthetic TAZ spanning the full commune polygon, keeping them in the same OD
framework. Commuter flows that cross Kreis boundaries follow the BA Pendleratlas
exactly (Kreis-level calibration applied on top of the TAZ gravity).

## Data

The TAZ data is **LOCAL-ONLY / proprietary**: RVB VISUM Verkehrszellen,
imported by `scripts/import_rvb_verkehrszellen.py` into
`eqasim-data/data/braunschweig/taz/rvb_verkehrszellen_epsg25832.parquet`.
This parquet is gitignored and must never be committed. The stage
(`braunschweig.data.spatial.taz`) fails fast with a clear error if the file is
absent.

## commune_id format (8-digit AGS) and ARS-12 normalisation

The TAZ stage's `commune_id` is the **8-digit AGS** (Amtlicher Gemeindeschluessel,
zero-padded); `import_rvb_verkehrszellen.py` / `load_taz_zones` validate this
8-digit form. The pipeline's population (`braunschweig.popsim.stage`) and employees
(`braunschweig.data.census.employees`) frames, however, key on the **12-digit ARS**
(Amtlicher Regionalschluessel). The WORK destination margin therefore normalises the
TAZ 8-digit AGS to the 12-digit ARS (via the `eqasim_common.spatial.codes` crosswalk,
the same map `employees.py` uses) before joining the authoritative employee totals,
and raises on any unmapped AGS so a 100% join miss can never silently zero the
attraction. The 5-digit Kreis prefix is identical in AGS and ARS, so the TAZ->Kreis
lookup is format-robust.

## Stage contract

`braunschweig.data.spatial.taz` returns a GeoDataFrame with columns
`[taz_id, commune_id, kreis, regiostar7, geometry]` in EPSG:25832.
Both `taz_id` and `kreis` are cast to `str` in `execute()` regardless of the
parquet's stored dtype, so downstream stages can rely on a consistent string key.

## BA Pendleratlas stays authoritative

Kreis-to-Kreis commuter flows from the BA Pendleratlas (files
`braunschweig.pendler_ein_path` / `braunschweig.pendler_aus_path`) remain the
calibration anchor for inter-Kreis commuting. The TAZ refinement operates
within communes and does not alter the Kreis-level flow totals.
