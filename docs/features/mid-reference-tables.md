# Reference data: MiD 2023 constraint tables (read this!)


Numerical reference values from the MiD 2023 regional sample
are **not** stored as Python literals. They live as CSV files under
`eqasim-data/data/braunschweig/mid/` and are loaded by
`braunschweig.data.mid.reference_tables`:

| File | Source table | Consumed by |
|---|---|---|
| `mid2023_P19_car_constraints.csv` | A P19 'jederzeit' | `braunschweig.data.mid.data` |
| `mid2023_P22_bicycle_constraints.csv` | A P22 'ja' | `braunschweig.data.mid.data` |
| `mid2023_P24_1_pt_subscription_constraints.csv` | A P24.1 (sum of subscription columns) | `braunschweig.data.mid.data` |
| `mid2023_P24_1.csv` | A P24.1 raw 9-column ticket-type breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P24_1_by_sex.csv` | A P24.1 sex margin (male/female × 9 ticket types) | same — IPF sex margin |
| `mid2023_P24_1_by_age.csv` | A P24.1 age margin (9 bands × 9 ticket types) | same — IPF age margin |
| `mid2023_H7_cars_by_kreis.csv` | H7 (Anzahl Autos im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H12_3_bikes_by_kreis.csv` | H12.3 (Fahrräder/Pedelecs im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H4_income_by_size.csv` | H4 (Ökonomischer Status × HH-Größe) | `braunschweig.data.census.household_income` |
| `mid2023_class_midpoint_eur.csv` | derived class-midpoint € lookup | `braunschweig.synthesis.population.enriched` |
| `mid2023_P17_1.csv` | A P17.1 raw 3-column licence breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P17_1_by_sex.csv` | A P17.1 sex margin (male/female × {ja,nein,k.A.}) | same — IPF sex margin |
| `mid2023_P17_1_by_age.csv` | A P17.1 age margin (9 bands × {ja,nein,k.A.}) | same — IPF age margin |
| `mid2023_status_by_hhtype_bundesland.csv` | ökon. Status × Haushaltstyp × Bundesland | `braunschweig.data.mid.status_by_hhtype` (economic-status Bayes, NDS base) |
| `mid2023_status_by_hhtype_raumtyp.csv` | ökon. Status × Haushaltstyp × RegioStaR-7 Raumtyp | same — within-NDS raumtyp tilt |

The *additional* tables `mid2023_P9.csv`, `mid2023_P12_1.csv`, `mid2023_P13.csv`,
`mid2023_P17_1.csv`, `mid2023_P24_1.csv` are produced by
`scripts/extract_mid_tables.py` (PDF parser). The two
`mid2023_status_by_hhtype_*.csv` are produced by
`scripts/extract_mid_status_by_hhtype.py` from local-only raw xlsx exports.

### Economic status from MiD household-type × region (`status_from_hhtype`)

`economic_status` (5 BMDV classes very_low..very_high) is determined from the
much stronger **Haushaltstyp × Region** predictor via Bayes instead of being
mapped 1:1 from the income €-class. For each synthetic household:

- it is mapped to one of the 11 substantive MiD Haushaltstyp categories
  (`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype`): 1-person
  by age band, 2-adult couple by youngest-adult age band, 3+ adults, child
  households bucketed by youngest-child age (<6 / <14 / <18), single parent
  (one adult + child, or upstream `hh_type=single_parent`);
- it is mapped to its home **RegioStaR-7** raumtyp (via `commune_id` → AGS-8 →
  `braunschweig.data.bbsr.regiostar`); Bundesland is always Niedersachsen;
- `P(status | hhtype, region) ∝ P(hhtype | status, region) · P(status | region)`
  with `P(hhtype|status,region)` = the column-% `share_pct` and `P(status|region)`
  from the per-(status,region) weighted bases. The **Bundesland (NDS) table is
  the base**; the raumtyp table is applied only as a **within-NDS tilt**
  (`P_raumtyp,region / P_raumtyp,national`) because the raumtyp table is national,
  not NDS-specific (`region_status_probabilities`).
- `economic_status` is sampled from this vector (seeded RNG, offset `+60413`),
  then `household_income` (€-class) is **re-derived from the sampled status**
  (`INCOME_CLASS_BY_ECONOMIC_STATUS`, the inverse of the H4 quintile map) so
  income and status agree; `household_income_eur` is then computed downstream by
  the existing INKAR class-midpoint scaling.

Flag-gated by `status_from_hhtype` (default **true**). OFF reproduces the exact
legacy path (commit c65399d): `economic_status` mapped 1:1 from the sampled
income €-class, income untouched → byte-identical. Households that cannot be
classified keep the legacy income-class status; the primary/fallback rate is
logged (CLAUDE.md no-silent-fallback). An **extension hook**
`bayes_status_given_hhtype_employment` is reserved for a future
`status × Erwerbstätigkeit` margin (multiplied in as a second Bayes factor).

Tests: `tests/test_status_from_hhtype.py`, additions in
`tests/test_economic_status.py`.

### PT ticket type (P24.1) — categorical & flatrate-derived `has_pt_subscription`

Each synthetic person receives a categorical attribute
`pt_subscription_type` ∈ `PT_TICKET_CATEGORIES` sampled from the per-Kreis
probability vector parsed from MiD 2023 Tabelle P24.1.
The boolean `has_pt_subscription` is then derived as

```
has_pt_subscription = pt_subscription_type ∈ PT_TICKET_FLATRATE
```

with `PT_TICKET_FLATRATE = {deutschlandticket,
monthly_or_annual_subscription, job_or_semester_ticket,
weekly_monthly_no_subscription}` — i.e. all ticket types that grant unlimited
rides on local PT during their validity. Since issue #329 the taxonomy is
English; the committed reference CSVs deliberately keep their codebook-German
column headers as the traceability link to the MiD instrument, translated once
at the loader boundary by `P24_RAW_COLUMN_BY_CATEGORY` in the same module. The
set is
defined in `braunschweig.data.mid.reference_tables` and re-used by the
MATSim person-attribute writer (`ptSubscriptionType` is written alongside
`hasPtSubscription`). The flatrate sum per Kreis matches the legacy
single-target seeding in `mid2023_P24_1_pt_subscription_constraints.csv`
within ±1 percentage point (covered by
`test_pt_flatrate_set_matches_legacy_kreis_share`).

The probability vector for each person is determined by a **three-margin
IPF (raking)** on the 4-way contingency table
`X[kreis, sex, age_bin, ticket_type]` with marginal targets from MiD P24.1:

- `mid2023_P24_1.csv`        — Kreis × ticket type
- `mid2023_P24_1_by_sex.csv` — Sex × ticket type (male/female)
- `mid2023_P24_1_by_age.csv` — Age × ticket type (9 bands: 14–17, 18–29,
  30–39, 40–49, 50–59, 60–64, 65–74, 75–79, 80+)

After convergence (200 iterations) every person in cell `(k, s, a)` is
assigned `P[k,s,a,:] = X[k,s,a,:] / Σ_c X[k,s,a,:]` and sampled
categorically.  Persons below `braunschweig.minimum_age.pt_subscription`
(default 0; effective floor is the MiD basis age 14) are deterministically
assigned `never_pt`.  Convergence diagnostics (max |Δ| per margin) are
printed by the `braunschweig.synthesis.population.enriched` stage — note
that MiD's three margins are independently rounded to integer percent and
therefore not internally consistent, so raking finds a least-squares
compromise within ~5 pp on the worst-case Kreis × ticket cell.

To **regenerate** the constraint CSVs from their pinned values run:

```powershell
python scripts/seed_mid_constraint_tables.py
```

This is the only supported way to update the values. Hard-coding new
percentages in Python modules is prohibited — add them to the seed
script (with a provenance comment) and re-run it instead.

Tests: `tests/test_mid_reference_tables.py` covers schema, loader
identity vs. legacy values, and seed-script idempotency.

### Driving licence (P17.1) — categorical & 3-margin IPF

`has_license` (renamed downstream to `has_driving_license`) is no longer
taken from KBA FE4.x data via the IPF model.  Instead each person above the
legal driving age (`LICENSE_MIN_AGE = 18`, regular Pkw-Führerschein Klasse
B; the BF17 / begleitetes Fahren option in Niedersachsen is intentionally
ignored) is assigned a `license_type`
∈ `LICENSE_CATEGORIES = ("ja","nein","keine_angabe")` sampled from a
**three-margin IPF (raking)** on the 4-way contingency table

```
Xl[kreis, sex, age_bin, license_category]
```

with marginal targets parsed from MiD 2023 Tabelle P17.1:

- `mid2023_P17_1.csv`        — Kreis × {ja,nein,k.A.}
- `mid2023_P17_1_by_sex.csv` — Sex × {ja,nein,k.A.}
- `mid2023_P17_1_by_age.csv` — Age × {ja,nein,k.A.} (9 MiD bands)

`has_license = (license_type == "ja")` (`keine_angabe` conservatively maps
to `False`, see `LICENSE_TRUE`).  Persons below 18 are forced to `"nein"`
deterministically.  The MiD margins are independently rounded to integer
percent and span 19 % … 94 %, so raking finds a least-squares compromise
within ~10 pp on the worst-case Kreis × age cell — diagnostics are printed
by the `braunschweig.synthesis.population.enriched` stage.

The legacy KBA-FE4-based `df["license"]` from
`braunschweig.ipf.attributed` is still produced (MiD overrides it inside
the enrichment stage), but is no longer the source of truth for
`has_license`.

Tests: `test_license_csv_has_all_kreise`,
`test_license_margin_csvs_exist_and_normalised`,
`test_license_margins_match_pdf_values`,
`test_license_ipf_three_margins_converges_on_synthetic_population`.
