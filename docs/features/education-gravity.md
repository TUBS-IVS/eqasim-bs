# Education gravity model (NDS school data)


All education levels are assigned by real-data distance-decay gravity models,
replacing the generic OSM hard-radius sampler: school-age pupils (6-19) to **real
Niedersachsen schools**, kindergarten children (0-5) to **real Kita facilities**
(LSN Plaetze), and university students (20+) to **real Hochschulen** (LSN
enrollment). The feature is flag-gated; with `education_gravity_enabled=false`
(default) the pipeline is byte-identical to the legacy OSM education assignment.

**Data.** The facilities table
`eqasim-data/data/braunschweig/schools/nds_schools_zgb.csv` (kept **local only**
for data-protection -- not committed; the `eqasim-data` tree is gitignored) is
built by `scripts/extract_nds_schools.py`
from the LSN directories `Schulverzeichnis_ABS_2025.xlsx` (allgemeinbildende
Schulen) + `Verzeichnis_der_BBS_2024.xlsx` (berufsbildende Schulen). One row per
**(school, level)**: a school offering several levels (e.g. a KGS) appears once
per level with that level's real pupil count as `capacity`. The script geocodes
addresses via OSM Nominatim (1 req/s, cached) and validates each point offline
against the local OSM education POIs (`osm_pois.parquet`, distance to the nearest
education feature; `validated = dist < 750 m`). Full provenance + the regenerate
command live in `eqasim-data/data/braunschweig/schools/README.md` and the
end-to-end trace in `.../schools/DATA_FLOW.md`. Hard-coding coordinates or
capacities in Python is prohibited - change the xlsx source or
`braunschweig/data/schools/typing.py` and re-run the script.

**Age -> level + capacity.** `braunschweig.data.schools.typing` maps each LSN
Schulgliederung (SGL) code to one of FOUR school levels and sums the matching
pupil counts: Primarbereich (SGL 00,01,03,04) -> `grundschule` (6-9);
Haupt/Real/Gym-SekI/IGS/KGS (11-19) plus the Oberschule/Foerderschule block
(40-69) -> `sekundar_1` (10-15); Gym/IGS/KGS Sek II (23,24,28,29) -> `oberstufe`
(academic upper secondary); all BBS pupils -> `bbs` (vocational). Adult forms
(Abendgymnasium 30, Kolleg 31) are excluded. Age 16-19 pupils are split per
person between `oberstufe` and `bbs` by `education_bbs_share` (default 0.681 =
NDS enrollment BBS 29336 / (BBS 29336 + Oberstufe 13745)). The split matters
because the two have very different trip lengths: BBS are sparse with a regional
catchment (long trips), the gymnasiale Oberstufe is local. The
Gymnasium/Realschule/Hauptschule mix within a level emerges automatically from
the real per-level capacity shares (no school-track choice is modelled). Note the
gravity age bands (0-5 / 6-9 / 10-15 / 16-19 / 20+) reclassify the boundary ages
relative to the legacy OSM sampler's 0-6 / 7-17 / 18+ split: with the flag ON,
age 6 moves from kindergarten to `grundschule` and ages 18-19 from university to
oberstufe/bbs. This only affects the ON path; the OFF path keeps the legacy
bands. LSN internal codes drop the Land prefix: official AGS-8 = `"03" + AGS6`,
Kreis-5 = `"03" + Kreis3`; the table is filtered to the ZGB-8 Kreise.

**The model (capacity-constrained distance decay).** Per level, the assignment is
a **rectangular doubly-constrained Furness balancing**
(`braunschweig.synthesis.locations.education_gravity_model.balance_doubly_constrained`,
the rectangular generalisation of `braunschweig.gravity.model.evaluate_gravity`):
pupils are rows (production target 1 each -> everyone is placed), schools are
columns (attraction target = real `capacity` **scaled to the pupil count** ->
schools fill in proportion to real Schuelerplaetze), friction
`f = exp(slope_level * d_km)`. Each pupil then draws a school proportional to the
**balanced flow row** - so distance decay shapes the assignment while the
double-constraint prevents a tiny nearby school from swallowing pupils that belong
in a larger one ("no 2-vs-10000"). A per-level max radius bounds the candidate set
(nearest-school fallback when a pupil has none in range). All randomness uses the
single `random_seed`. Kindergarten (0-5) uses the SAME doubly-constrained capacity
gravity on the Kita facilities (see below); university (20+) uses a singly-
constrained decay (see below). The per-person stage
`braunschweig.synthesis.locations.education_gravity` produces the legacy output
schema `[person_id, commune_id, location_id, geometry]` and is swapped in by the
flag-gated wrapper
`braunschweig.locations.synthesis.replacement_education_gravity` (aliased to
`synthesis.population.spatial.primary.locations`).

Config keys (defaults in the stage's `configure`):
`education_gravity_enabled` (false), `education_gravity_slope_by_level`
(`{grundschule, sekundar_1, oberstufe, bbs}`),
`education_gravity_max_radius_km_by_level` (includes `kindergarten`),
`education_gravity_max_iterations` (50), `education_gravity_tolerance` (1e-3),
`nds_schools_path`, plus the kindergarten + university keys below.

**Kindergarten (Kita) children (age 0-5).** Routed through the SAME
doubly-constrained capacity gravity as the schools, on real Kita facilities
(`braunschweig.data.schools.kita_facilities`). Capacity = the LSN
Kindertageseinrichtungen **Plaetze** per Einheits-/Samtgemeinde (local-only
`eqasim-data/data/braunschweig/schools/nds_kitas_zgb.csv` from LSN table K2300112,
extracted by `scripts/extract_nds_kitas.py`; ZGB-8 = 832 facilities / 56084
Plaetze). The Samtgemeinde Plaetze are distributed across the unit's OSM
kindergarten POIs by area: each POI's LSN unit code is derived from its 12-digit
ARS commune_id as `ARS[2:5] + ARS[6:9]` (Kreis + Verband), with a 3-digit Kreis
fallback for the kreisfreie Staedte (BS/SZ/WOB, which LSN lists at Kreis level) --
this needs no separate Samtgemeinde membership table. The per-RS7 slope is
calibrated against the MiD 2023 Tabelle 43 **0-6** column (~1.5-2.3 km
straight-line; RS7 72 floors at ~1.6 km = the nearest urban Kita). The LSN table
K2300223 (children in Kita by age group + Besuchsquote) is a local validation
reference, not a model input. Config: `nds_kitas_path`, the `kindergarten` entries
in `education_gravity_slope_by_level_rs7` / `education_gravity_max_radius_km_by_level`
(8 km).

**University (Hochschule) students (age 20+).** University students are routed
through a dedicated
**singly-constrained** distance-decay model (`assign_by_decay`): each student
draws an institution `~ enrollment_j * exp(slope * d_ij)` within
`education_university_max_radius_km` (150 km), with a nearest-campus fallback.
Singly-constrained (NOT the doubly-constrained school model) is the key choice:
far universities (Goettingen, Hannover) have huge enrollment that is mostly
non-resident, so only the distance decay -- not a hard capacity target -- should
govern how far the local commute tail reaches. Destinations come from
`braunschweig.data.schools.university_facilities`: real LSN SS2025 enrollment per
institution (local-only `eqasim-data/data/braunschweig/schools/nds_hochschulen.csv`,
seeded by `scripts/seed_nds_hochschulen.py`) -- inside ZGB the per-commune
enrollment is spread across the commune's OSM university buildings by area (TU BS +
HBK pooled in 03101; Ostfalia split across 03158/03102/03103; TU Clausthal 03153);
each of the 12 surrounding institutions (Hannover cluster, Goettingen, Hildesheim,
HAWK, plus the cross-border Magdeburg OVGU / HS Magdeburg-Stendal and Hochschule
Harz, Leuphana Lueneburg) is a single curated campus point. The single national
`education_university_slope` (-0.1415) is calibrated so the mean ZGB student commute
matches the **Destatis MZ 2024 Hochschule** mean (~15.2 km straight-line); the
result is ~91 % local (TU BS / Ostfalia / Clausthal / HBK) and ~9 % commuting to
Hildesheim / Hannover / Harz / Magdeburg / Goettingen. Config:
`education_university_slope`, `education_university_max_radius_km`,
`nds_hochschulen_path`.

**Enrollment report (debug / calibrate).**
`python -m braunschweig.analysis.run_education_validation --working-directory
<cache> --sampling-rate <r> --output-dir <out>` writes
`school_enrollment_vs_capacity.csv` (per school: capacity vs assigned pupils
scaled to 100 %, fill_ratio) and `level_summary.csv` (per level: pupil count,
mean/median straight-line school-commute km), so over-/under-filled schools and
the slope calibration are immediately visible.

**Per-(RegioStaR-7, level) slope calibration (MiD Tabelle 43).** The decay slope
is differentiated by the pupil's **home RegioStaR-7** class so urban pupils (short
trips) and rural pupils (long trips) decay at their own rate. Each pupil's home
RS7 comes from a spatial join of the home point to `data.spatial.municipalities`
(the 12-digit ARS is converted to the 8-digit AGS via
`braunschweig.data.bbsr.regiostar.ars_to_ags8` before the RS7 merge -- without
this every pupil silently falls back to the scalar slope). The per-RS7 slopes
live in `education_gravity_slope_by_level_rs7` (nested `{level: {rs7: slope}}`;
default `None` -> scalar `education_gravity_slope_by_level`, like
`gravity_slope_by_regiostar7`). They are calibrated against **MiD 2023 Tabelle 43**
("Kita- und Schulweglaengen nach Raumtyp und Altersgruppe", reference CSV
`eqasim-data/data/braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv` seeded
by `scripts/seed_mid_t43_school_distance.py`, loaded by
`braunschweig.data.mid.school_distance`). The MiD age groups map 0-6 ->
kindergarten, 7-10 -> grundschule, 11-13 -> sekundar_1, 14-17 -> `oberstufe`; MiD
routed lengths are divided by a detour factor (1.3) to a straight-line target. The
vocational `bbs`
level has no per-RS7 MiD target -- BBS distance is benchmarked against the
**Destatis Mikrozensus 2024** national school-trip distribution by school type
(`braunschweig.data.mikrozensus.school_distance`, CSV seeded by
`scripts/seed_mikrozensus_school_distance.py`): the banded BBS distribution gives
a national straight-line mean of ~15.8 km, applied as the same target to every RS7.

`scripts/calibrate_education_slopes.py` runs the calibration on the 25 % synthesis
(`cache_bs_25pct`): the WHOLE level is assigned each round (per-pupil slope vector
by home RS7) and each RS7's mean trip distance is moved toward its target by a
per-RS7 **bisection** (`calibrate_level_per_rs7`; bisection is stable on the noisy
means of small rural cells). Calibrating cells in isolation is wrong -- the
capacity constraint, scaled to a pupil subset, forces filling out-of-catchment
schools. Tiny/sparse cells off by > 1.5 km whose slope is NOT at the steep bound
are **shrinkage-regularised** to the pupil-weighted mean slope of the converged
cells of the same level; cells AT the steep bound (slope ~ -3.0) are kept -- there
the target is simply below the nearest-school distance (rural BBS / rural
Oberstufe), a legitimate structural floor, not noise. The committed evaluation
(`--output-dir eqasim-data/data/braunschweig/mid/education_calibration/`:
`calibration_results.csv`, two figures, `calibration_summary.md`) shows
grundschule, sekundar_1 and bbs hit their targets across RS7 (bbs RS7 77 floors at
~20 km -- the nearest rural BBS is already that far); oberstufe converges for the
larger cells, while the tiny rural cells (RS7 75/76/77, ~40-50 pupils at 25 %) are
regularised and would sharpen at a higher sampling rate. Re-run the script
(`--bbs-share` controls the upper-secondary split) and paste its YAML to update the
slopes; do not hand-tune.

Tests: `tests/test_school_typing.py`, `tests/test_school_readers.py`,
`tests/test_school_facilities.py`, `tests/test_education_gravity_model.py`,
`tests/test_education_gravity_stage.py`, `tests/test_education_validation.py`,
`tests/test_mid_school_distance.py`, `tests/test_mikrozensus_school_distance.py`,
`tests/test_university_facilities.py`, `tests/test_extract_nds_kitas.py`,
`tests/test_kita_facilities.py`, `tests/test_calibrate_education_slopes.py`,
`tests/test_regiostar_fill.py` (the `ars_to_ags8` helper).
