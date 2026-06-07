# Model realism roadmap (beyond MiD calibration) — 2026-06-07

Methodical audit of the eqasim-bs demand/supply model to find **structural and
content improvements that make the model more realistic**, explicitly EXCLUDING
"recalibrate against MiD" (that is covered separately). Produced by a 6-agent
parallel scan of the synthesis pipeline (`braunschweig/...`), the eqasim core
(`synthesis/...`), and the Java fork (`../eqasim-java-bs`). Each item lists the
current method (file:line), the realism gap, a concrete improvement with a named
public German/regional data source, and Effort / Realism-impact / Repro-risk.

## The dominating cross-cutting finding: synthesised-but-dead attributes

Several attributes are carefully synthesised and then **discarded or behaviourally
inert**. Fixing these is the highest leverage-to-effort work in the whole model,
because the data and synthesis already exist:

| Attribute | Synthesised at | Problem | Fix |
|---|---|---|---|
| `household_income_eur` (INKAR-scaled €) | `enriched.py:902` | never added to `PERSON_FIELDS`, never written to MATSim XML; the **categorical placeholder string** (`"5000+"`, incommuters `"3000"`) is written instead | add to `PERSON_FIELDS`, write numeric attr |
| income (any form) | written as `householdIncome` | **mode choice has no income term** — cost utility interacts with *distance only*; income changes no decision | port eqasim income×cost interaction into BS estimators |
| `number_of_cars` (MiD H7 per Kreis) | `enriched.py:931` | fleet generator emits **one car per *person***, ignoring household car count entirely | generate vehicles per household |
| `couple` | `attributed.py:610` | hardcoded `False` although `hh_type` + couple-pairing know the truth | derive from `hh_type` / pairing |
| `studies` | `attributed.py:611` | hardcoded `False`; not a matching key → every student gets a non-student activity day | set from university assignment; add to matching |
| `socioprofessional_class` | `attributed.py:612` | hardcoded `0` although it IS an eqasim matching key → dimension collapses | map from employment+age |

---

## Tier A — highest leverage, low/med effort, low/med risk (reconnect what exists)

### A1. Make income behaviourally active (port income×cost interaction into the BS Java estimators)
- **Current:** `BraunschweigCarUtilityEstimator.java:32-48` / `BraunschweigPtUtilityEstimator.java:51-54` compute cost disutility = `betaCost * interaction(distance/refDistance, lambda) * cost` — **distance only, no income**. `BraunschweigPersonVariables`/`Predictor` never read income. Zurich variant proves the hook exists (`ZurichCarUtilityEstimator.java:48` uses `householdIncome_MU`).
- **Gap:** All income synthesis is behaviourally dead — low- and high-income households react identically to fares/fuel/parking. No distributional/equity analysis of any pricing policy (D-Ticket, parking, fuel) is possible.
- **Fix:** Add `householdIncome_MU` to `BraunschweigPersonVariables` + predictor, add `referenceHouseholdIncome_MU` / `lambdaCostIncome` to `BraunschweigModeParameters`, change cost utility to the canonical eqasim form `betaCost * (income/ref)^lambda * interaction(distance) * cost`. Calibrate λ to the eqasim value (≈ −0.8) or German VoT-by-income.
- **Effort:** M · **Impact:** High · **Risk:** Med (changes outputs; needs ASC recalibration)

### A2. Write the realistic continuous income to MATSim (enabler for A1)
- **Current:** `matsim/scenario/population.py:77` writes categorical `household_income`; the INKAR-scaled `household_income_eur` never reaches XML; incommuters hardcode `"3000"` (`incommuters.py:54,513`).
- **Fix:** add `household_income_eur` to `PERSON_FIELDS`, write as numeric `householdIncomeEur`; read it in `BraunschweigPersonPredictor`. Replace incommuter constant with an origin-Kreis VGRdL draw.
- **Effort:** S · **Impact:** Med (enabler) · **Risk:** Low

### A3. Reconnect the three dead constants: `couple`, `studies`, `socioprofessional_class`
- **Current:** `attributed.py:610-612` set all three to constants (`False`, `False`, `0`).
- **Gap:** `couple`/`studies` are computable from existing internal state (`hh_type`, couple-pairing in `household_composition.pair_adults_sex_aware`, university assignment in `education_gravity`); `socioprofessional_class` is an eqasim **matching key** that collapses to one value, dropping a behavioural dimension the matcher expects.
- **Fix:** derive `couple` (+ partner linkage) from the formed household type; set `studies` from the university-destination assignment and add it to `matching_attributes`; map employment+age → eqasim SPC categories.
- **Effort:** S–M · **Impact:** Med–High · **Risk:** Low (reconnecting existing data)

### A4. Generate vehicles per household from `number_of_cars` (not one car per person)
- **Current:** `synthesis/vehicles/cars/default.py:19-24` emits one identical car per *person* (incl. children, carless adults); `number_of_cars` (MiD H7, region mean ≈1.05 cars/HH, ~15% zero-car) is written to the population but never sizes the fleet.
- **Fix:** instantiate exactly `number_of_cars` vehicles per household, bound to the household, distributed to licensed members. No new data.
- **Effort:** M · **Impact:** High (fleet/parking/emissions) · **Risk:** Low

### A5. Make `car_availability` consistent with cars + licence (stop the 3-IPF contradiction)
- **Current:** licence (P17.1 IPF), `number_of_cars` (H7 IPF) and `car_availability` (P19 IPF) are three independent per-person/per-household IPFs raked only on {Kreis, sex, age}; the eqasim core cars-vs-licences→`car_availability` coupling (`synthesis/population/enriched.py:91-101`) is **overwritten** by the marginal-only P19 IPF (`braunschweig/.../enriched.py:201-232`). Result: `car_availability="all"` can occur in a 0-car household or for a person without a licence.
- **Fix:** draw `car_availability` *conditionally* within licence×cars cells (logical floor `none` if no licence or 0 cars, then rake the residual to the P19 target); reinstate the `some` category. No new data.
- **Effort:** M · **Impact:** High · **Risk:** Med (needs a test that joint constraints hold AND P19 marginal still matches)

### A6. Couple PT subscription to car availability and to employment/student status
- **Current:** PT subscription IPF (`enriched.py:446-629`) rakes only on {Kreis, sex, age}; `employed`/`studies` exist but are not margins; `jobticket_semesterticket` can be assigned to retirees/children; the strong carless↔PT-pass correlation is absent (independent RNG offsets).
- **Fix:** add `car_availability` (sequence the car IPF first) and `employed`/`studies` as margins, or split `jobticket_semesterticket` conditioned on status (semester ticket only for students — near-compulsory in the VRB Solidarmodell). Source: MiD P24.1 × Pkw-Verfügbarkeit / × Erwerbsstatus cross-tabs (published), VRB semester-ticket agreement.
- **Effort:** M · **Impact:** High · **Risk:** Med (needs MiD cross-tab; reorders stages → cache/output change)

---

## Tier B — core behavioural realism (high impact, high effort/risk)

### B1. Re-anchor mode-choice parameters to German/ZGB values (currently Munich/IDF)
- **Current:** `BraunschweigModeParameters.buildDefault()` — every ASC/beta is a Munich/Île-de-France literal; most ASCs are **hand-nudged** away from the estimate with the original commented out (`car.alpha_u = 0.4; // -0.201465` …). No mode-parameters file is passed in the pipeline, so these defaults run. Implied car VoT ≈ 8.2 €/h is not a German value.
- **Gap:** the entire DECISION layer reproduces Munich preferences; ASC tuning is undocumented and unreproducible (violates the project's no-hardcoding/traceability rule).
- **Fix:** re-estimate the logit on MiD 2023 regional / MOP, or at minimum re-anchor VoT to BVWP 2030 / EWS German values and calibrate ASCs against the MiD modal split with a documented procedure; commit a provenance-tagged parameters CSV passed via `getModeParametersPath()`.
- **Effort:** L · **Impact:** High · **Risk:** High

### B2. Replace the Munich PT fare model with a VRB-native tariff
- **Current:** `PT_COST_MODEL_NAME = "MunichPtCostModel"` → `BraunschweigPtCostModel.java` keeps Munich `shortPrice=1.9`, `basePrice_h=8.0`, the Munich price ladder and the "M" inner-zone special case; only the zone-IDs were remapped to BS-Hbf rings (`vrb/zones.py`). Documented fare residuals (diff 0 → 3.90€ vs Stadttarif 3.50). D-Ticket = any `hasPtSubscription`→0 regardless of ticket type.
- **Fix:** VRB-native fare model reading the real VRB Preisstufen table keyed by ring difference, proper Stadttarif tier, D-Ticket tied to `PT_TICKET_FLATRATE` membership.
- **Effort:** M · **Impact:** High · **Risk:** High

### B3. German vehicle fleet — powertrain / Euro / age / BEV (KBA + HBEFA)
- **Current:** every vehicle is `default_car`, HBEFA `tech/size/emission="average"`, French `critair="Crit'air 1"`, `technology="Gazole"` (diesel), `age=0`, `euro=6`. No petrol/diesel/BEV/PHEV split, no age/Euro/size classes → any emission/energy analysis is meaningless. **`eqasim-data/data/braunschweig/fz3_2025.xlsx` (KBA per-Gemeinde registrations) is already downloaded but unwired.**
- **Fix:** German fleet sampler (structure exists in `synthesis/vehicles/cars/fleet_sampling.py`, currently French-coupled) driven by KBA FZ 1.2 (fuel type, NDS), FZ 3 (per-Gemeinde, on disk), FZ 15 (Euro + age); map technology/Euro → HBEFA German naming (matsim-berlin/eqasim-germany have ready lookups); add an `electric_share`/powertrain dimension (Wolfsburg/Salzgitter make this locally distinctive).
- **Effort:** L · **Impact:** High (emissions/electromobility) · **Risk:** Low–Med (official KBA data)

### B4. Replace ENTD-2008 (French) trip timing with a German HTS donor
- **Current:** `hts: entd` (`config_local_braunschweig_25pct.yml:16`); departure times + activity durations copied verbatim from the 2008 French national survey (`synthesis/population/trips.py`, `activities.py`), only ±30 min household jitter added.
- **Gap:** work/school start times, shopping/leisure timing and thus network peak-spreading are donor-cultural, not local — a first-order driver of *when* congestion occurs.
- **Fix:** match/re-time from MiD 2023 trip-level microdata (or SrV) time-of-day distributions by purpose.
- **Effort:** M–L · **Impact:** High · **Risk:** Med

---

## Tier C — supply side (network, PT, freight, validation)

### C1. Real VRB/regional GTFS instead of the gtfs.de free national feed
- **Current:** only `eqasim-data/data/gtfs/latest.zip` = gtfs.de "latest-free" (DELFI), rail-centric, local bus/tram aggregated; `dayWithMostServices` may pick an atypical day.
- **Fix:** real VRB/VBN GTFS (NDS open-data / DELFI Soll-Fahrplandaten / gtfs.de pro) + DB long-distance; pin an explicit `gtfs_date`.
- **Effort:** M · **Impact:** High · **Risk:** Low

### C2. Freight / commercial-vehicle layer (currently none)
- **Current:** no carrier/freight/jsprit/truck demand anywhere; only a `truck` mode kept on the network with no demand. LKW/LCV ≈ 10–15% of vehicle-km, higher on A2/A39/A391 + VW/Salzgitter corridors.
- **Fix:** MATSim small-scale-commercial-traffic (Wirtschaftsverkehr) generator or jsprit carriers from establishments + Destatis Güterverkehr / KiD trip rates; BVWP/BASt matrix for through-traffic (ties into the cordon).
- **Effort:** L · **Impact:** High · **Risk:** Med

### C3. Traffic-count validation (BASt) ± Cadyts calibration
- **Current:** link flows never confronted with measured volumes; only modelled-vs-modelled grid comparison exists.
- **Fix:** MATSim `Counts` from BASt Dauerzählstellen (hourly open data) + city/NDS Verkehrszählung; validation reporting, optionally `CadytsModule`.
- **Effort:** M · **Impact:** High · **Risk:** Low (independent observed data strengthens defensibility)

### C4. Network attribute refinement + signal delay
- **Current:** link capacity/freespeed/permlanes are raw pt2matsim OSM class defaults; no real lanes/maxspeed (Tempo-30), no signalised-intersection delay; detailed geometry discarded.
- **Fix:** post-process from OSM `maxspeed`/`lanes`/`oneway` (pbf already downloaded); reduce freespeed at `highway=traffic_signals` nodes or use `SignalSystems`/`Lanes` on main corridors.
- **Effort:** M–L · **Impact:** High · **Risk:** Med

### C5. Enable the buffered cordon network + external/through demand
- **Current:** default network is osmosis-clipped to ZGB with no buffer (`network_clip.py:41-45`); A2/A7/A39/A391 truncated at the boundary; cordon feature flag-gated OFF. Through-traffic and boundary capacity missing.
- **Fix:** build on the buffered extent (machinery exists: `cordon_network_source_buffer_m`, `RunScenarioCutter`, gate injection) + external demand from BASt motorway counts + BA Pendleratlas.
- **Effort:** M (machinery exists) · **Impact:** High · **Risk:** Med

### C6. PT vehicle capacity / crowding
- **Current:** transit vehicles are pt2matsim defaults; SwissRailRaptor schedule-based, no crowding → PT never fills up; BSVG tram trunk overcrowding invisible.
- **Fix:** realistic per-mode capacities (tram/bus/rail) from VDV/operator data; enable capacity-constrained PT.
- **Effort:** M · **Impact:** Med · **Risk:** Med

### C7. Secondary-facility attractiveness weight + opening hours
- **Current:** facilities have boolean `offers_shop/leisure/other` only, no size/weight, no opening hours; carla solver scores by distance only; a big mall and a kiosk are equiprobable at equal distance.
- **Fix:** attractiveness weight (OSM/ALKIS floor-area, Verkaufsfläche) fed to the carla solver; write `opening_hours` (OSM tag) into facilities.
- **Effort:** M · **Impact:** Med · **Risk:** Low

---

## Tier D — population structure & additional socio-demographics

### D1. Categorical employment status (currently binary SvB flag)
- **Current:** `employed` from GENESIS SvB head-count only; "not SvB" lumps unemployed + pensioners + students + Minijob; incommuters hardcode `employed=True`.
- **Fix:** `employment_status` ∈ {full_time, part_time, marginal/Minijob, unemployed, retired, in_education, NILF} from BA Beschäftigungsstatistik (Voll-/Teilzeit + geringfügig per Kreis) + Mikrozensus Erwerbsstatus; anchor per-person pay with BA Entgeltstatistik (median by Kreis × Vollzeit).
- **Effort:** L · **Impact:** Med–High · **Risk:** Med–High (schema change; gate + validate)

### D2. Continuous income distribution instead of class-midpoint × Kreis-scale
- **Current:** income € = `class_midpoint_eur[class] × inkar_scale[kreis]` — 5 discrete midpoints, single multiplicative Kreis mean, no within-class spread, no right tail.
- **Fix:** draw from a fitted distribution (log-normal per quintile, or the already-vendored `bhepop2`) anchored on Destatis VGRdL "verfügbares Einkommen je Einwohner" at **Gemeinde** level (finer than INKAR Kreis) + INKAR/Mikrozensus percentiles for shape.
- **Effort:** M–L · **Impact:** Med · **Risk:** Med

### D3. Income disaggregated to earners (equivalised, earner-conditioned)
- **Current:** one household-income class per person as a function of `household_size` only; uniform within household; ignores number of earners.
- **Fix:** condition the draw on `(household_size, number_of_earners=count of employed adults)` via Mikrozensus, or compute OECD-equivalised per-capita income (no new data) as the person budget feeding A1.
- **Effort:** M · **Impact:** Med–High · **Risk:** Med

### D4. Migration background / nationality
- **Current:** absent. Strong mobility correlate (lower car/licence, higher PT/walk) in MiD/SrV, independent of age/income.
- **Fix:** add as IPF margin / categorical draw from Zensus 2022 migration tables (Gemeinde/Kreis × age × sex); join into HTS matching only if MiD records it.
- **Effort:** M · **Impact:** Med · **Risk:** Med (Gemeinde cells partly disclosure-suppressed)

### D5. Education attainment (Bildungsabschluss)
- **Current:** absent; SPC ≡ 0 (see A3). Primary explanatory variable for mode choice / car ownership in eqasim's design.
- **Fix:** populate from Zensus 2022 "Bildungsstand"/"Stellung im Beruf" (Kreis × age × sex) + Mikrozensus SUF for the joint.
- **Effort:** M · **Impact:** Med–High · **Risk:** Med

### D6. Real microdata seed for the IPF (instead of flat Cartesian seed)
- **Current:** IPF seed is a flat product of (commune × sex × age × employed × licence) with weight 1.0; every correlation not explicitly a margin is forced to max-entropy independence.
- **Fix:** seed from Mikrozensus Scientific-Use-File / Campus File (FDZ) or the Zensus 2022 SUF (announced), rake to existing marginals — standard PopulationSim "sample + control totals".
- **Effort:** L · **Impact:** High · **Risk:** Med (licensed SUF; document anonymisation)

### D7. WG / multigenerational household split
- **Current:** everything non-single/couple/parent → `other_multi`, age-unstructured; a 3-person student WG and a 3-generation household look identical. Relevant in a university city.
- **Fix:** split via Zensus 2022 1000A-2087 (Lebensform: Mehrgenerationen- vs Nicht-Verwandten-Haushalte); add a WG shell (young unrelated adults, no parent-child gap) + multigenerational shell; calibrate WG share to the loaded student headcount.
- **Effort:** M · **Impact:** Med · **Risk:** Med

### D8. Population projection to the scenario year
- **Current:** Destatis 12411 (31.12.2024) totals + Zensus 2022 structure; no projection. `target_year` only selects INKAR columns.
- **Fix:** if a future/2026 analysis year is intended, apply BBSR Raumordnungsprognose 2045 or LSN regionalised Bevölkerungsvorausberechnung as Kreis×age×sex factors raked into margins + BBSR household-size trend; make projection year an explicit logged config. Else document the base year as a stated assumption.
- **Effort:** M · **Impact:** Med (High for forecasts) · **Risk:** Med

### D9. Smaller behavioural realism items
- **BF17 / begleitetes Fahren (age 17):** deliberately excluded (`LICENSE_MIN_AGE=18`); high uptake in Niedersachsen. Optional `license_bf17_enabled` sampling age-17 from MiD P17.1 14-17 band + KBA Fahrerlaubnisbestand, written as restricted (`accompanied_driving`) car access. M / Med / Med.
- **Deutschlandticket vintage:** MiD 2023 captured only the launch months; no scenario-year/price. Add `pt_subscription_scenario_year` reweight to a VDV-published penetration target (price 49→58€ 2025). M / High / Med.
- **Graduated parking zones:** currently a single binary "Paris" ring at flat 3 €/h under the `isParis*` attribute names; no capacity/search/zone tariff. Replace with BS Bewohnerparkzonen / Parkraumbewirtschaftung tariffs; rename to `isUrbanCore`. M / Med / Med.
- **Walk/bike distance caps:** walk/pt always available regardless of distance (long walk killed only by steep beta). Add explicit max-distance availability from MiD trip-length percentiles. S / Med / Low.
- **Shared/emerging modes:** no car-sharing (stadtmobil/Flinkster), bike-sharing, e-scooter, ride-hailing. L / High (if policy-relevant) / Med.
- **P+R / Bike+Ride intermodality + PT reliability:** no access-mode chaining to PT; PT comfort = single bus-vs-rail dummy. L / Med / Med.
- **Spatial resolution of licence/PT IPFs:** stop at 8 Kreise although RS7 (RegioStaR-7) machinery exists in the gravity/education models; add an RS7 margin (MiD publishes P17.1/P24.1 by RegioStaR). M / Med / Low.

---

## Suggested sequencing

1. **Tier A first** (A1–A6): mostly reconnecting already-synthesised data; transforms
   income/cars/couple/studies/SPC from dead attributes into active model drivers.
   A2+A3 are S-effort, A1/A4/A5/A6 are M. This is where realism-per-effort is highest.
2. **B1 (mode parameters)** is the single biggest realism gap and a prerequisite for
   A1 to be meaningful — but it is L-effort/high-risk; pair it with the MiD modal-split
   target and commit provenance-tagged CSVs (also satisfies the no-hardcoding rule).
3. **B3 (fleet)** rides on A4 and uses the already-downloaded KBA fz3 file.
4. **C1/C3/C5** materially improve the supply side and validation; C2 (freight) is the
   largest single new module.
5. **Tier D** deepens socio-demographic realism; D6 (real seed) is foundational but
   licensing-gated.

All synthesis changes are seeded — validate output equivalence / changes for any
non-identical change, and add fallback-rate instrumentation per the MANDATORY rule.
