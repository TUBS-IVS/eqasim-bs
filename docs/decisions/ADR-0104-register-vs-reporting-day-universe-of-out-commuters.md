# ADR-0104 · 2026-09-05 · Register vs reporting-day universe of out-commuters
- **Status:** active
- **Numbering:** ADR-0104 is the next free id. It was checked on 2026-09-05 across ALL local and
  remote branches (`git ls-tree docs/decisions/` for every ref, not only `main`), the highest id
  found being ADR-0103; ids are append-only, so a colliding draft on a sibling branch would be
  renumbered here rather than the other way round (the mechanism ADR-0099 records for the
  ADR-0098 collision).
- **Context:** The model's work OD is anchored to the **BA Pendleratlas register**
  (`braunschweig.data.census.pendler`, 48,340 flow pairs in the run's `provenance.json`): it
  records WHO WORKS WHERE for all socially insured employees and carries **no commuting
  frequency** at all. The day model then sends every such person to that workplace on the one
  simulated weekday. The references the model is judged against -- SrV 2023 and MiD 2023 -- are
  **reporting-day surveys**: they record who travelled where on ONE day. These are two different
  universes, and nothing in the pipeline currently converts the first into the second.
  The Phase 0 SrV distance measurement made the consequence visible. ADR-0103's amendment of
  2026-09-04 found that the pre-registered inter-Gemeinde gap (aggregate band EMD **0.085** on the
  `inter` scope) does NOT survive restricting both sides to destinations inside the eight ZGB
  Kreise: the sensitivity variant `inter_zgb` measures **0.021**, `ok` in every cell. The gap is
  carried by the polygon-external destinations -- i.e. by exactly the long-distance out-commuters
  the register places and the survey day does not see.
  The Phase A measurement (run manifest `commute-day-state-phase-a-2026-09-05`, this branch,
  100 % i329 population) sizes that cohort. Of **304,900** workers with an assigned workplace,
  **39,150 (12.84 %)** have a fabricated external (EXT) destination. By assigned commute-distance
  class (euclidean home->workplace x detour factor 1.3): `lt10` 141,352 (0.4636), `10_25` 71,528
  (0.2346), `25_50` 54,311 (0.1781), `50_100` **21,515** (0.0706), `100_200` **5,235** (0.0172),
  `gt200` **10,959** (0.0359). The two long classes the day-state model would act on therefore
  hold **37,709** workers (21,515 at 50-100 km plus 16,194 at or above 100 km), of which
  **31,851** sit in the external scope and 5,858 in the internal one.
  This record fixes the finding, the assumptions and the decision that follows from it. The design
  it records is the design spec of 2026-09-04 (local working document, not a durable home); every
  number below is read from a committed file named next to it.
- **Measured evidence (Phase A, all committed):**
  - **MiD 2023 reporting-day work location by commute-distance class**
    (`eqasim-data/data/braunschweig/mid/mid2023_workday_location_by_commute_distance.csv`,
    P_GEW-weighted row shares). "At the workplace" falls from **0.5906** (`lt10`) to **0.3135**
    (`100_200`); "at home" rises from **0.0815** to **0.3285**; "did not work" stays essentially
    flat at 0.2613 / 0.2670 / 0.2602 / 0.2296 / 0.2697 across the five classes. This monotone
    substitution of home for workplace WITH distance, at a roughly constant not-working rate, is
    the mechanism the day-state model reproduces. Universe (in the file's own header): `arbwo == 1`
    (reporting day is a weekday) AND `M_HOFF == 1` (asked the home-office module) AND `P_STARB1`
    in (1, 2, 9); n_unweighted 49,527, of which 4,635 (9.4 %) carry no valid distance and appear
    only in the `all` row. Two caveats stated in the same header: classes are LEFT-INCLUSIVE
    `[a, b)` and self-reported `P_ARB_ENTF` heaps on round numbers (36.2 % of valid distances are
    multiples of 5 km; 2,159 persons report exactly 10 km, 133 exactly 100 km), so class COUNTS
    are convention-sensitive while the weighted STATE SHARES agree within 0.005 between
    conventions; and the **200 km top-code** (202 of the 783 persons in the thin `100_200` class)
    means MiD cannot resolve anything beyond 200 km at all.
  - **Home-office-day donor pool**
    (`eqasim-data/data/braunschweig/mid/mid2023_home_office_donor_pool.csv`): **8,026** weekday
    module donors who worked at home (`P_STARB1 == 1`, `starb2 == 1`), by distance class `lt10`
    1,860 / `10_25` 1,944 / `25_50` 1,462 / `50_100` 696 / `100_200` 276, plus **1,788** with no
    valid distance. The thinnest cross-classified cells are `50_100` with children AND active
    escort (**76** donors) and `100_200` with children AND active escort (**30**) -- too thin for
    a stable per-cell draw on their own.
  - **SrV 2023 work participation, regional**
    (`eqasim-data/data/braunschweig/srv/srv2023_work_participation_by_kreis.csv`, zgb row,
    n_persons **8,016**, weight GEWICHT_P_ZENSUS): full home-office day **0.1418**, made a work
    trip **0.6511**, neither **0.2071**. Per Kreis the home-office share spans **0.059**
    (Salzgitter 03102) to **0.179** (Gifhorn 03151). Wolfsburg (03103) is not surveyed and carries
    n_persons 0 with NaN shares.
  - **Model work participation against that reference** (manifest validation entry 1;
    `work_participation_by_kreis.csv`): 540,425 employed persons in the ZGB, 267,194 with a work
    trip = **0.4944** against the SrV **0.6511**, i.e. **-15.67 pp**; the model is below the
    reference in EVERY surveyed Kreis, from -12.27 pp (03101) to -20.55 pp (03102). Only
    `share_work_trip` is comparable: the model has no day state, so its `share_no_work_trip`
    (0.5056) is the SUM of the two SrV remainder states and is never differenced against either.
    This delta is **NOT independent of existing machinery and is NOT decomposed**: the synthesis
    already applies hard SrV participation controls (feature record
    `docs/registry/features/srv_participation_controls.yml`, whose `work_participation` target is
    defined over ALL persons, not over the employed) and the i329 run manifest
    `docs/runs/100pct-allfeat-i329-2026-08-24.yml` documents a donor-bound participation deficit.
    How much of the -15.67 pp is that documented deficit, how much is the different universe, and
    how much is a genuine day-state gap is **UNKNOWN** from this run.
  - **EXT destination geometry** (manifest entry 3; `ext_destination_distances.csv`): the
    worker-weighted share of the 39,150 EXT workers whose model distance class equals the class of
    the plain BA Kreis-centroid-to-centroid distance is **0.8258**, measured over **646** (home,
    destination) Kreis pairs (0 pairs without a VG250 centroid, 1 without a BA flow). Over all
    304,900 workers, **0.4261** lie within 5.0 km of a class edge, i.e. their class would flip
    under a small change of the distance or the detour factor. The DIRECTION of the 17.4 %
    disagreement was not aggregated: unknown.
  - **Donor-vs-assigned class mismatch** (manifest entries 4-6;
    `donor_vs_assigned_diagnostics.json`, `donor_vs_assigned_class*.csv`). With `P_ARB_ENTF` as
    the donor distance only **80,855** of 304,900 workers (**26.52 %**) are comparable, and among
    them `share_assigned_gt_donor` is **0.1831** (14,806 workers). The reason is STRUCTURAL, not a
    broken join (all four join rates are 100.00 %): `P_ARB_ENTF` is a question of the MiD
    **home-office module**, so it is valid for 53.34 % of the 151,571 workers whose donor is
    in-module and for **0.00 %** of the 153,329 whose donor is not. With the donor's first valid
    **work-trip length** (`MiD2023_Wege.csv`, `W_ZWECK == 1`, `0 < wegkm < 1000`) the comparable
    base is **263,420** workers (**86.40 %** coverage; 41,480 = 13.60 % have no such length) and
    `share_assigned_gt_donor` is **0.2171** (57,177 workers). Donor universe: ALL 304,900 workers
    have a donor with `arbwo == 1`, with raw `ST_WOTAG` codes 1-5 occurring and 6/7 absent
    entirely (no code-to-weekday mapping is asserted -- the repository carries no committed
    statement of that coding).
- **Decision:**
  - **Build the commute-day-state model** as designed, because the register/reporting-day
    mismatch above is a modelling gap and not a data defect. Each employed person with an assigned
    workplace receives a `commute_day_state` in {`at_workplace`, `home`, `absent`} as a person
    attribute, exported with the population.
  - **Re-draw only upwards.** The state is re-drawn ONLY when the ASSIGNED distance class is
    strictly higher than the DONOR's class; otherwise the donor's own day already encodes its
    class's not-working and home-office behaviour and is passed through unchanged. This avoids
    double-counting the survey's own home-office mass.
  - **Keep probability from the committed MiD table, named by column.**
    `P(keep) = share_at_workplace(assigned class) / share_at_workplace(donor class)`, both read
    from the `share_at_workplace` column of
    `mid2023_workday_location_by_commute_distance.csv` -- never typed from a report PDF. The
    quantity is explicitly NOT `1 - share_did_not_work`: that column is essentially FLAT across
    the classes (0.2613 / 0.2670 / 0.2602 / 0.2296 / 0.2697), so a rule built on it would return
    a ratio near 1 for every pair and be a no-op. What varies with distance -- and what the model
    must therefore act on -- is the substitution of `share_at_home` (0.0815 -> 0.3285) for
    `share_at_workplace` (0.5906 -> 0.3135), which is exactly the ratio above.
  - **`home` = a complete donor day, not chain surgery.** A person drawn to `home` receives the
    COMPLETE trip chain (purposes, modes, times, order) of a MiD home-office-day donor, prepared
    with the same time-offset logic as `braunschweig.popsim.trips_stage`. Hard matching criteria,
    never coarsened: active escort legs, children under 14 in the household, car availability.
    Soft criteria in order: distance class, then the popsim matching attributes (`sex`,
    `age_class`, `employed`, `has_license`, `household_size_class`, `urban_class`), with a
    declared coarsening hierarchy; every coarsening step and the not-replaceable share are counted
    and logged as rates (CLAUDE.md fallback-transparency rule). A person with no remaining donor
    stays `at_workplace` and is counted.
  - **`absent` above the far threshold.** Above `commute_day_far_threshold_km` (200 km) a person
    becomes `absent` with probability `commute_day_absent_share_far`; an `absent` person's trips
    are removed entirely, and the attribute is what distinguishes "absent" from "stayed home".
  - **Two-view trips architecture.** The pre-assignment view (`synthesis.population.trips`,
    `...activities`) stays as it is and keeps feeding commute distances, primary candidates and
    primary locations; a reporting-day view (`synthesis.population.trips.final`,
    `...activities.final`) feeds everything that needs the finished day (secondary chainsolvers,
    the MATSim population). This is what keeps the day state -- which depends on the assigned
    distance -- out of a dependency cycle with the location assignment.
  - **Flag default ON** (`commute_day_state_enabled`), with the OFF path proven byte-identical by
    an explicit test, per the project's default-ON convention.
  - **Phase A is measured first and decides nothing.** The numbers above state differences against
    committed references; no target is set and no threshold is attached in Phase A. Phase B builds
    the model and is judged against the six checks below. **These six, as written here, ARE the
    durable pre-registration**: the design spec of 2026-09-04 is only where they were first
    drafted, and it is a gitignored local working document, so this record -- not that file -- is
    what Phase B is held to. They are fixed BEFORE the model exists, and are not to be
    retro-fitted to its outcome.
    1. Realised `at_workplace`/`home`/`absent` shares and the share of employed persons without a
       work trip, regionally against SrV **0.1418 / 0.6511 / 0.2071**, at a tolerance of
       **+/- 3 pp on the regional aggregate only**; per-Kreis values are reported, never gated.
       **ASSUMPTION -- the +/- 3 pp band is a pre-registered tolerance chosen a priori in the
       2026-09-04 design, not derived from any committed source.** The reason it is a band and
       not a point: the SrV per-Kreis home-office cells rest on 663-2,268 persons under a
       stratified PSU design over ~44 selected municipalities and are assumption-grade for a full
       Kreis (data record `srv2023_work_participation`), so only the regional aggregate
       (n 8,016) is treated as gate-worthy, and even there with a declared slack.
    2. Inter-Gemeinde work bands re-measured with the Phase 0 stages: the `100_plus` band
       **below 3 %**, and no deterioration of the bands up to 50 km against the 2026-09-04
       baseline EMD. **ASSUMPTION -- the 3 % bound is a chosen operating bound, pre-registered a
       priori, with NO reference behind it.** The committed SrV reference cannot supply one: every
       `100_plus` column of `srv2023_commute_distance_by_kreis.csv` is exactly **0.0** in all 15
       rows (all three scopes, raw and shrunk), i.e. the survey records no such commute at all,
       and ADR-0102 Assumption 2 records why that zero is itself suspect (GIS-invalid work trips
       carry a heavier long-distance tail). A bound of 0 % would therefore assert the survey's
       structural blind spot as truth; 3 % is a deliberately non-zero, deliberately unsourced
       operating bound, and it must be labelled as such wherever the check is reported.
    3. Cordon out-commuter gate volumes before/after, against an expectation restricted to
       `at_workplace` persons.
    4. Donor matching diagnostics: coarsening rate per step, not-replaceable share,
       missing-donor-distance share, pool size per cell.
    5. Sensitivity of `commute_day_absent_share_far`, 1.0 vs 0.6.
    6. OFF-path byte identity, a 25 % proof run first and a 100 % proof in the next scheduled
       production run.
- **Amendments to the design, forced by the Phase A measurement:**
  1. **The donor distance measure changes.** The design named `P_ARB_ENTF` as the PRIMARY donor
     distance with the work-trip length as a fallback. On this population that is the wrong way
     round: `P_ARB_ENTF` reaches only **26.52 %** of workers because it is a home-office-module
     question (0.00 % validity outside the module). The **donor's first valid work-trip length**
     (`W_ZWECK == 1`, `0 < wegkm < 1000`) becomes the **PRIMARY** source, covering **86.40 %**,
     and `P_ARB_ENTF` is kept as a **cross-check only**; the two are reported side by side and
     are never merged into one number. **ASSUMPTION:** a reporting-day trip length proxies the
     person's usual commute distance. It does not in general -- a donor who worked at home or did
     not work that day has no length, which is precisely the state the model wants to represent --
     so Phase B must report the rate at which the primary source is used, per class, and must not
     let the residual 13.60 % pass silently.
  2. **The class boundary convention is fixed as LEFT-INCLUSIVE `[a, b)`**, matching the committed
     MiD tables, with the heaping sensitivity disclosed rather than removed: under a
     right-inclusive convention `lt10` would hold 21,259 instead of 19,100 MiD persons and
     `100_200` 650 instead of 783, while the weighted state shares move by less than 0.005. The
     model side is fragile in the same place for a different reason: **0.4261** of workers sit
     within 5 km of a class edge.
  3. **The -15.67 pp work-participation gap is a separate open question, not a target of this
     model.** It is recorded here and in the manifest as measured-and-undecomposed, linked to
     issue **#244** and to `srv_participation_controls`. The day-state model will move this number
     (a `home` or `absent` person makes no work trip, so it moves it FURTHER DOWN), which is
     exactly why it must not be treated as this model's fit criterion: Phase B check (1) compares
     the THREE-WAY split against SrV, not this single share.
- **Assumptions (explicit, none of them observed):**
  1. **Far threshold 200 km.** Taken from the MiD `P_ARB_ENTF` top-code: MiD carries no evidence
     at all above 200 km, so the threshold is where the reference stops, not where behaviour
     changes. Configurable (`commute_day_far_threshold_km`).
  2. **Absent share 1.0 above that threshold**, i.e. a > 200 km commuter is away on the simulated
     weekday (weekly commuters being away four of five weekdays and travelling on the fifth).
     Configurable (`commute_day_absent_share_far`); the pre-registered sensitivity is 1.0 vs 0.6.
     This is the single largest uncertainty in the design and it governs 10,959 workers.
  3. **The `100_200` class keeps the MiD value** rather than an absent share, although it surely
     also contains weekly commuters -- deliberately conservative: MiD still measures 0.3135 of
     that class AT THE WORKPLACE on the reporting day.
  4. **Escort-duty persons are never `absent`.** A donor day with active escort legs
     (`W_ZWECK == 6`) evidences presence at home; such persons may become `home` but not `absent`.
     This biases parents away from `absent` by construction.
  5. **MiD national ratios transfer to the ZGB.** There is no regional MiD sample; SrV is used for
     VALIDATION only and is not a donor pool (no donor-format trips, small n).
  6. **Detour factor 1.3** converts euclidean to routed kilometres, the same convention as the
     Phase 0 distance work; model distances are therefore not routed distances.
  7. **A home-office-day donor represents the person's home-office day**, given the hard and soft
     matching criteria above. The person's ORIGINAL non-work trips are replaced wholesale; beyond
     escort, household coherence of the day is informal in the model anyway.
- **Rationale and rejected alternatives:** The argument for acting at all is the shape of the
  evidence, not the size of any single number: MiD shows the workplace/home substitution rising
  monotonically with distance while not-working stays flat, and the model has 37,709 workers in
  exactly the classes where that substitution is strongest, 31,851 of them at fabricated external
  points. Four alternatives were considered and rejected:
  - **Scale down the BA out-commuter mass.** Rejected: it breaks the register anchor the project
    requires. The register is the best available statement of who works where; the defect is in
    the DAY, not in the stock.
  - **Condition the donor draw on the assigned distance instead of modelling a day state.**
    Rejected as circular: the assigned distance comes from the OD the donor's own trips helped
    build, so conditioning the donor on it feeds the model's own output back as evidence.
  - **Chain surgery -- remove the work activity and keep the rest of the day.** Rejected: it
    produces a hybrid day nobody lives (a commuter's chain minus its anchor), whereas MiD carries
    8,026 real home-office days that can be transplanted whole.
  - **Treat everything above 100 km as `absent` outright.** Rejected against the committed table:
    MiD still measures **0.3135** of the `100_200` class at the workplace on the reporting day.
  Two honesty bounds on all of the above. First, this is a **measurement against references, not
  a validation**: nothing here establishes that the model reproduces observed travel behaviour,
  and no convergence or equilibrium claim is involved. Second, the reference itself is bounded --
  ADR-0102 Assumption 2 records that GIS-invalid SrV work trips carry a heavier long-distance
  tail than GIS-valid ones, so a one-day regional survey plausibly UNDER-observes irregular
  long-distance commuting; the model may over-produce out-of-ZGB workplaces AND the survey may
  under-see them, and Phase A does not separate the two.
- **Consequences:**
  - Issue **#359** stays re-targeted exactly as ADR-0103's amendment left it: the lever is WHICH
    destination a commuter is sent to and on which days, not per-Kreis friction inside the
    destination Kreis (`inter_zgb` is `ok` everywhere).
  - A **Phase B plan** is written before any model code, per the project's workflow; the checks
    listed under Decision are the pre-registered ones and are not to be retro-fitted to the
    outcome.
  - The **cordon expectation must be restricted to `at_workplace` persons** before it can be used
    as a check on this model; today it is derived from the same BA figures the model is anchored
    to and is therefore self-referential for out-commuters.
  - **Cache cost, deliberate:** the alias switch to the `.final` stage names devalidates secondary
    locations and everything downstream ONCE, even with the flag OFF. For scale: the secondary
    chainsolver accounted for **9,599 s (about 2 h 40 min)** of the i329 100 % run, but that is
    the run's TOTAL solve time over FOUR executions in three phases (2,389.3 s + 2,306.9 s |
    2,568.0 s | 2,334.9 s, `docs/runs/100pct-allfeat-i329-2026-08-24.yml`), not the cost of one
    devalidation -- a single re-solve is roughly a quarter of it. The honest statement is that
    the order of magnitude is hours, and the exact cost of one re-solve at 100 % is not pinned by
    any committed measurement.
  - **Server hazard, recorded so it stops surprising us:** a freshly created detached worktree run
    against the SHARED i329 cache devalidates and re-executes a block of data/location/gravity
    stages it never targeted. This has now happened three times on record -- the 2026-09-03
    baseline's aborted attempt 1, its attempt 2, and this Phase A run, where synpp devalidated
    FOURTEEN stages for a single analysis target. It also resets the i329 stage-hash bookkeeping,
    after which entries rewritten under an unchanged hash are no longer checkable against their
    predecessor. Proposing an issue for a documented safe procedure is left to the maintainer;
    none is opened by this record.
  - `docs/registry/features/commute_day_state_measurement.yml` carries
    `validation.state: measured_vs_reference` with the Phase A run id and names this ADR under
    `introduced.adr`; the stage record lists it under `decisions`; both new data records
    (`mid2023_workday_location`, `srv2023_work_participation`) point at it.
- **Evidence:** the run manifest `docs/runs/commute-day-state-phase-a-2026-09-05.yml` and the
  committed measurement directory
  `eqasim-data/data/braunschweig/calibration/commute_day_state_phase_a_2026-09-05/`
  (`work_participation_by_kreis.csv`, `assigned_distance_classes.csv`,
  `ext_destination_distances.csv`, `donor_vs_assigned_class.csv`,
  `donor_vs_assigned_class_trip_length.csv`, `donor_vs_assigned_diagnostics.json`, `summary.md`,
  `provenance.json`, `run_log.txt`) -- every model-side number above is read from those files;
  the committed references
  `eqasim-data/data/braunschweig/mid/mid2023_workday_location_by_commute_distance.csv` and
  `eqasim-data/data/braunschweig/mid/mid2023_home_office_donor_pool.csv`
  (data record `docs/registry/data/mid2023_workday_location.yml`) and
  `eqasim-data/data/braunschweig/srv/srv2023_work_participation_by_kreis.csv` (data record
  `docs/registry/data/srv2023_work_participation.yml`); the stage record
  `docs/registry/stages/braunschweig.analysis.synthesis.work_participation_by_kreis.yml` and the
  feature record `docs/registry/features/commute_day_state_measurement.yml`; ADR-0102 (the SrV
  reference definition and its Assumption 2 tail finding) and ADR-0103 (the Phase 0 verdicts and
  the `inter_zgb` amendment) with the run manifests
  `docs/runs/srv-primary-distance-baseline-2026-09-03.yml` and
  `docs/runs/srv-primary-distance-baseline-2026-09-04.yml`; the i329 production run
  `docs/runs/100pct-allfeat-i329-2026-08-24.yml`; issue #244 (parent) and #359. The design this
  record adopts is the commute-day-state design spec of 2026-09-04, a LOCAL working document
  under `docs/superpowers/specs/` which is gitignored and is therefore NOT a durable home: every
  fact it carries that matters is restated here, in the registry records, or in the manifest.
