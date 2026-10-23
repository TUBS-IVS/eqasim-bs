# ADR-0157 · 2026-10-23 · Adults choose their education location among all education facilities by distance

- **Status:** accepted (implemented; to be measured in a rerun, see the status line at the end)
- **Numbering:** 0157 checked free across all local and remote branches on 2026-10-23 (0153-0156 are the sibling
  branches of #465; the transit boarding decision takes 0158).
- **Issue:** #465 (root cause behind mechanism 4 of the 68 stuck residents of run `i465-wpe-smoke-1pct-2026-10-23`).
  **Relates to:** ADR-0024 (education gravity models), ADR-0155 (every agent chooses its modes once).
- **Owner decision (2026-10-23):** option A of three ("20-29 Jahre weiter zur Hochschule, ab 30 irgendein
  Bildungsstandort mit Distanzabfall"); the alternatives were an OSM class "adult education" (B) and no change (C).

## Context

- `braunschweig.synthesis.locations.education_gravity` assigns the education location by age alone: 0-5 Kita, 6-19
  NDS schools, and EVERY person aged 20+ a university facility (`assign_by_decay`, enrolment-weighted, slope
  -0.1415/km calibrated to the Mikrozensus 2024 mean distance to a Hochschule of about 15.2 km, radius 150 km). The
  survey trip of the person (mode, duration) plays no role.
- Synthesis population of run `i465-wpe-smoke-1pct-2026-10-23` (1 %; felix `~/wt-i487/analysis/adult_education_run1.txt`):
  338 persons aged 20+ with an education trip, 439 trips; 198 (45 %) end more than 15 km from home, 46 within 2 km;
  126 of them are walk (77) or bicycle (49) trips by the survey to a location more than 5 km away. Example: person
  834024 (43, employed, no car, no licence) has a 5-minute survey walk to education and was placed at a university
  22 km away; never chosen again by the mode choice, it walked 4 x 28 km.
- Adult courses (Volkshochschule, further education, language and driving schools) are not in the facility data.

## Decision

1. From `education_adult_min_age` (ASSUMPTION, 30) on, an education activity is no longer a university one: the
   person chooses among ALL education facilities, the NDS schools of every level and the university facilities, with
   the SAME weight (pupil capacities and enrolments say nothing about adult courses), by the singly-constrained decay
   `assign_by_decay` with `education_adult_slope` (ASSUMPTION, -0.15 per km) within `education_adult_max_radius_km`
   (ASSUMPTION, 30 km; nearest-facility fallback outside, counted by `assign_by_decay`). The slope and radius are the
   scalar Sekundarstufe I values: adult courses often take place in school buildings. Not calibrated.
2. Persons aged 20 to `education_adult_min_age` - 1 keep the university model unchanged.
3. `education_adult_min_age: null` switches the band off (every person aged 20+ goes to a university, as before).
   The three keys are validated at configure and pinned in `configs/base_bs.yml`.
4. One log line per run: persons placed, facilities, the straight-line distance median, mean and 90th percentile, and
   how many chose a university.

## Rejected options

- **B, an OSM facility class "adult education"** (amenity college, language school, driving school, community
  centre): more realistic, but a new data stage with its own coverage questions; can replace the school-building
  proxy later.
- **C, no change:** the first-replanning mode choice (ADR-0155) makes the mode fit the distance, but the 22 km
  location for a 5-minute survey walk stays.
- **Keep the enrolment weights for the adults:** a university's 20,000 places would outweigh every school within
  about 15 km and reproduce the far locations.

## Consequences

- Output change by design: education locations of persons aged 30+ move closer to home on average; university
  facilities receive fewer adult visitors. No committed observed reference of adult education trip distances exists
  in the repository; the log reports the assigned distribution for a later comparison with the survey.
- **Status:** implemented; NOT part of the first 1 % rerun of the #465 fixes (launched before this decision); to be
  measured in a follow-up rerun. A smoke, not a validation.
