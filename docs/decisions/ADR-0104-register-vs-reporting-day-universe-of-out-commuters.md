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
    `[a, b)` (chosen by convention -- spec wording, continuous model distances -- and applied
    IDENTICALLY on the donor and the model side) and self-reported `P_ARB_ENTF` heaps on round
    numbers (36.2 % of valid distances are multiples of 5 km; 2,159 persons report exactly 10 km,
    133 exactly 100 km). **Both class COUNTS and weighted STATE SHARES are convention-sensitive**:
    counts strongly (`lt10` moves between 19,100 and 21,259 persons depending on the convention),
    shares by up to a MEASURED maximum absolute deviation of **0.0237** (2.37 pp) -- MEASURED on
    the 2026-09-05 extraction (commit `43f74008`) by
    `commute_day_state_reference.measure_bin_convention_deviation`, which rebuilds the table a
    second time with right-inclusive bins; the figure is recorded verbatim in the committed CSV's
    own header. (An earlier draft of this finding asserted the shares "stay robust ... within
    0.005" without deriving it; the corrected, measured value is roughly 4.7x larger, and the
    claim that only counts are convention-sensitive was itself false -- CLAUDE.md "No invented
    reference values" forbids carrying the old, unmeasured figure or framing forward once a real
    measurement exists.) And the **200 km top-code** (202 of the 783 persons in the thin
    `100_200` class) means MiD cannot resolve anything beyond 200 km at all.
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
    `share_assigned_gt_donor` is **0.2171** (57,177 workers). Finding 5 of the whole-branch review
    (final fix wave, commit `43f74008`) corrected a latent defect in this figure: `wegkm` is a raw
    trip length, never subject to the MiD `P_ARB_ENTF` 200 km top-code, so the top-code special
    case must be disabled for it (`classify_commute_distance(..., topcode_km=None)`); re-measured
    on felix with the fix applied, `donor_vs_assigned_class_trip_length.csv` is BYTE-IDENTICAL to
    the pre-fix version -- no donor's work-trip length in this population is exactly 200.0 km, so
    the numbers above are unchanged by the correction. Donor universe: ALL 304,900 workers
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
     `100_200` 650 instead of 783, while the weighted state shares move by at most **0.0237**
     (measured, not the earlier unmeasured "0.005" claim -- see the Measured evidence section
     above). The model side is fragile in the same place for a different reason: **0.4261** of workers sit
     within 5 km of a class edge.
  3. **The -15.67 pp work-participation gap is a separate open question, not a target of this
     model.** It is recorded here and in the manifest as measured-and-undecomposed, linked to
     issue **#244** and to `srv_participation_controls`. The day-state model will move this number
     (a `home` or `absent` person makes no work trip, so it moves it FURTHER DOWN), which is
     exactly why it must not be treated as this model's fit criterion: Phase B check (1) compares
     the THREE-WAY split against SrV, not this single share.
- **Amendments forced by the Phase B proof run (2026-09-05, fix wave A):** the run
  (`docs/runs/commute-day-state-phase-b-proof-100pct-2026-09-05.yml`) blocked on check 1, could
  not move check 2, and could not write an ON population at all. The three amendments below
  change WHAT is measured or matched; they are written down here, rather than applied quietly,
  because altering a check after seeing its own result is precisely what pre-registration exists
  to prevent. **None of them moves a threshold**: the +/- 3 pp band, the 3 % `100_plus` bound,
  `cds_max_states_outside_employed_share` = 0.05 and
  `commute_day_max_not_replaceable_share` = 0.5 all stand unchanged.
  1. **Check 2 is measured over the reporting-day commuters, not over the assigned workplaces**
     (ruling R5). `braunschweig.analysis.synthesis.commute_distance_by_kreis` reads
     `synthesis.population.spatial.primary.locations`, i.e. WHERE people work, which this model
     never changes -- the ON and OFF location frames of the proof run were byte-identical, and so
     was every band share, so check 2 as executed could not have registered any effect of the
     model whatever it did. The SrV reference bands are defined on persons who made a work trip on
     the reporting day, so the model side is now restricted to workers whose drawn state is
     `at_workplace`. The assigned-workplace measurement is kept beside it as
     `commute_by_kreis_all_assigned.csv` and is not deleted: it remains the right universe for
     questions about the workplace ASSIGNMENT, and keeping both makes the change auditable.
     **The two halves of check 2 are therefore read from DIFFERENT tables, and this is the
     binding instruction for every future evaluation of it:** the `100_plus < 3 %` bound is read
     from the reporting-day table `commute_by_kreis.csv`, because it is a statement about the
     commutes actually travelled; the "no deterioration of the bands up to 50 km against the
     2026-09-04 baseline EMD" half is read from `commute_by_kreis_all_assigned.csv`, because the
     baseline it compares against was measured on the assigned-workplace universe and a
     comparison across two different universes would attribute a universe change to a model
     change.
  2. **Check 1's guard distinguishes a join failure from a universe difference** (ruling R6). The
     guard fired at 12.37 % on a residual in which every person_id matched a population row: those
     37,706 workers have an assigned workplace but are not flagged `employed` by
     `synthesis.population.enriched`. That is a difference between the model's worker cohort and
     the employed cohort SrV surveyed -- a finding to report, not a defect to abort on -- so it is
     now counted (`n_workers_not_employed`, per Kreis in `commute_day_state_shares.csv` and in
     the check-1 section) and warned about, while states that resolve to no population row at all
     keep the fatal semantics the guard was written for. **The gap itself is NOT explained by this
     amendment**: why the model assigns workplaces to 12.37 % more persons than the population
     calls employed is an open question, tracked with the -15.67 pp participation gap above.
  3. **A fourth hard matching criterion: the education anchor** (ruling R7). ADR-0104's three hard
     criteria (active escort, children under 14, car availability) do not cover FIXED PURPOSES the
     receiving person cannot anchor. 36 of the 5,086 persons drawn to `home` (0.71 %) received a
     donor chain containing an `education` activity while having no education location, and the
     secondary chainsolver raised on the `None` origin/destination that produces. A donor whose
     chain contains an education activity is therefore eligible only for a person who has an
     education location; a donor without one still matches anyone. Of the three candidate fixes,
     re-anchoring the activity (inventing a location the person does not have) and dropping the
     leg (silently editing a transplanted real day) were rejected in favour of not transplanting
     such a day at all -- the person is then downgraded to `at_workplace` by the model's existing,
     already-instrumented not-replaceable path, so the effect is visible in the diagnostics rather
     than hidden inside a repaired chain. The cost is a smaller effective donor pool for persons
     without an education location, which the coarsening cascade and the not-replaceable rate
     both report.
  Two further fixes of the same wave (rulings R8 and R9) are DIAGNOSTIC ONLY and change no
  measurement: the plan replacement no longer assembles replaced rows column by column (657,888
  pandas `PerformanceWarning`s, a 254 MB run log), and an immobile donor (`n_trips == 0`, 32.5 %
  of the pool by construction) is now counted apart from a donor absent through a `donor_id`
  mismatch, which the run conflated into one unreadable 27.3 % rate.

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

## Proof 2026-09-06 (Phase B, 100 % on the cached i329 population)

Phase B was proved in three runs on 2026-09-05/06 -- (A) the OFF analysis path, (B) the complete
ON line down to the written population, (C) the `commute_day_absent_share_far` 0.6 sensitivity --
at 100 % on the cached i329 population, under the code state `0be490ad` (the branch plus fix wave
A). Run manifest `docs/runs/commute-day-state-phase-b-proof-100pct-2026-09-06-rerun.yml`;
committed aggregates
`eqasim-data/data/braunschweig/calibration/commute_day_state_phase_b_proof_100pct_2026-09-06_rerun/`.
It SUPERSEDES the partial first attempt
(`docs/runs/commute-day-state-phase-b-proof-100pct-2026-09-05.yml`), which aborted on check 1's
guard and on the secondary chainsolver and never wrote an ON population; that manifest stays as
the historical record of the three defects it found. **No threshold was moved, by fix wave A or
here**, and every number below is copied from the manifest or from the committed artefact named
beside it.

### Per-check result against the pre-registration

1. **State shares against SrV, +/- 3 pp on the regional aggregate -- FAIL.** Universe: the
   540,425 employed persons with a ZGB home Kreis (`on/commute_day_state_shares.csv`, `zgb` row).
   Measured against the committed SrV `zgb` row (`share_work_trip` 0.6511, `share_home_office_day`
   0.1418, `share_neither` 0.2071, n_persons 8,016): `at_workplace` **0.4810** (-17.01 pp),
   `home` **0.0082** (-13.35 pp), `absent` **0.0051** (-20.20 pp) -- all three OUTSIDE the +/- 3 pp
   band, so the check fails on the aggregate. Beside them, `share_no_workplace` **0.5056** (the
   employed persons for whom no state is drawn at all, because they have no assigned workplace)
   and `share_employed_no_work_trip` 0.5188 against the SrV remainder 0.3489 (+16.99 pp).
   `n_workers_not_employed` = **37,705** workers with an assigned workplace whom
   `synthesis.population.enriched` does not flag employed: counted and warned about per ruling R6,
   not fatal. Per-Kreis rows are reported, never gated.
2. **Inter-Gemeinde work bands -- FAIL on the 3 % bound, PASS on no-deterioration.** The
   `100_plus < 3 %` half is read, per the R5 amendment, from the REPORTING-DAY table
   `on/commute_by_kreis.csv`'s `zgb`/`inter` row (n_model **153,607** -- distinct from the
   296,599-of-304,900 all-scope `at_workplace` worker count check 1 reports above):
   **0.073740** (7.37 %) against the pre-registered 3 % -> FAIL; the same row's
   EMD is 0.069395 with noise floor 0.012915 over n_ref 2,593, classification `ok`. The
   no-deterioration half is read from the ASSIGNED-workplace table
   `on/commute_by_kreis_all_assigned.csv`: byte-identical AS PRODUCED (LF) on the server to
   `srv_distance_baseline_2026-09-04/commute_by_kreis.csv` and to run A's OFF
   `commute_by_kreis.csv`; the committed copies are CONTENT-identical after EOL normalisation (the
   2026-09-04 copy was committed with CRLF, 10,232 B against 10,204 B). Nothing up to 50 km moved
   at all -- PASS.
3. **Cordon out-commuter volumes, restricted to `at_workplace` persons -- PASS.**
   `on/commute_day_state_scaling.json`: 33,143 of the 39,150 external workers are `at_workplace`,
   share **0.846564**, scaling the outbound register expectation from **60,705** to **51,393** SvB
   (-9,312, -15.34 %). All three joins complete (`n_workplace_unresolved`,
   `n_states_without_work_location`, `n_work_locations_without_state` all 0). This is a
   before/after of the EXPECTATION, not of simulated gate counts: no MATSim iteration ran.
4. **Donor-matching diagnostics -- PASS for three of the four listed items.**
   `on/state_diagnostics.json`: coarsening levels 0/1/2/3/4/5/6 = **3,786/0/352/391/23/534/0** over
   the 5,086 persons drawn to `home`; not-replaceable **0 of 5,086** (0.00 %, far below
   `commute_day_max_not_replaceable_share` 0.5); missing donor distance 0 and the PRIMARY
   donor-distance source (Amendment 1 above) at **1.0 in every assigned class**, so its 50 %
   warn bound never fired. The FOURTH item, **pool size per matching cell, is `unknown`**: the
   diagnostics dict records the level a person matched at, not the number of donors the cell held.
   Ruling R7, newly measured: 49 of the 8,026 donors carry an education leg, 4,954 of the 5,086
   `home` persons have no education location and **4,911** of them lost at least one donor to the
   new criterion, yet not-replaceable stayed at 0.00 %. Ruling R9 settles the first run's
   unreadable 27.3 %: **1,435** of the 5,086 matched donors (28.2 %) are flagged `is_immobile` in
   the raw MiD Wege file and correctly give a trip-less day, and **0** donors had zero rows
   although their donor travelled. The R9 (and R8) counters are read from the committed
   `run_log_excerpts.txt`, not from a structured artefact -- see the follow-ups below.
5. **Sensitivity of `commute_day_absent_share_far`, 1.0 vs 0.6 -- PASS.** Same population, same
   seed: 1.0 gives `home` **5,086** / `absent` **3,215**, 0.6 gives `home` **6,345** / `absent`
   **1,956**, with `at_workplace` **296,599** IDENTICAL in both and the change confined to the
   `gt200` class. Over check 1's employed denominator the 0.6 variant moves `home` to 0.0103
   (-13.15 pp) and `absent` to 0.0031 (-20.40 pp) -- it does not move check 1 across its band.
6. **OFF-path byte identity and the proof run itself -- PASS.** The ON population differs from the
   OFF one for EXACTLY **8,301** persons (5,086 `home` + 3,215 `absent`): `households.csv`,
   `vehicles.csv` and `vehicle_types.csv` byte-identical, `persons.csv` byte-identical after
   removing the single appended `commute_day_state` column, 862,163 trip blocks and 1,122,903
   activity blocks byte-identical, and no person present only in the ON file. Row counts: trips
   **3,369,427** ON against **3,392,698** OFF (-23,271, -0.686 %), activities 4,500,631 against
   4,523,902 for all 1,131,204 persons in both. The OFF POPULATION itself was not re-written by
   this re-run (run A held the two analysis stages only), so its byte identity to
   `output_bs_100pct_i329` stands from the 2026-09-05 manifest; what this run re-measured is the
   OFF ANALYSIS path under fix-wave-A code, where every CSV and `decisions.json` are
   byte-identical to the 2026-09-05 OFF outputs.

### Decision

- **The flag stays default ON at the current parameters** (`commute_day_state_enabled` true,
  `commute_day_far_threshold_km` 200.0, `commute_day_absent_share_far` 1.0). Reasons, each read
  from the artefacts above: checks 3, 4, 5 and 6 pass; the OFF path is unchanged in substance
  (every OFF CSV and `decisions.json` byte-identical, and the OFF population byte-identical to the
  i329 production output per the 2026-09-05 manifest); the ONLY population change is the 8,301
  re-drawn far commuters, with every other person's plan byte-for-byte identical; and the model's
  own effect on the quantity it was built to move is separable and in the intended direction --
  the reporting-day `zgb`/`inter` `100_plus` band is **0.100083** with the flag OFF
  (`off/commute_by_kreis.csv`'s `zgb`/`inter` row, n_model **161,805** -- of the all-scope
  304,900 workers) and **0.073740** with it ON
  (`on/commute_by_kreis.csv`'s `zgb`/`inter` row, n_model **153,607** -- of the all-scope
  296,599 `at_workplace` workers), i.e. **-2.63 pp**, which is
  the removal of the re-drawn non-travellers from the reporting-day universe and nothing else (the
  ON and OFF workplace-location caches are byte-identical, so no workplace ASSIGNMENT moved).
- **Check 1 fails BY CONSTRUCTION of the comparison, not by miscalibration of the model** --
  labelled as a READING of the numbers, not as a measurement. The model's `home` state covers only
  the far commuters it re-draws (0.8 % of the employed universe), whereas SrV's 14.2 % home-office
  days cover ALL employed persons; the model's implicit home-office and not-working behaviour is
  inside `share_no_workplace` = **0.5056**, i.e. 50.6 % of employed persons have no work trip on
  their donor day, against the SrV remainder of 34.9 % (20.7 % neither + 14.2 % home office). The
  arithmetic driver of the failure is therefore the pre-existing participation gap (-15.67 pp with
  the flag OFF, Phase A; -16.99 pp with it ON), which this record already declines to treat as
  this model's fit criterion (Phase A amendment 3, issue #244, `srv_participation_controls`).
  **The tolerance is NOT relaxed**: check 1 is recorded as FAILED at its pre-registered form and
  stays that way.
- **Check 1 is proposed for RE-SPECIFICATION as follow-up work, and is not re-specified here.**
  The comparison that would be informative -- the model's `home` plus its "no work trip because
  the donor worked at home" mass against SrV's home-office days -- requires a home-office
  ATTRIBUTION the population does not carry: a donor who made no work trip is not distinguished
  from a donor who worked at home. Writing that re-specification into this record after seeing the
  result is exactly what pre-registration exists to prevent, so it is named as future work and
  nothing is changed.
- **Check 2's 3 % bound is not met, and closing the gap is a PARAMETER decision reserved for the
  maintainer.** The mechanism is fully visible in the committed artefacts: of the 16,194 workers
  whose ASSIGNED class is at or above 100 km, **11,327 stay `at_workplace`** (4,106 in `100_200`,
  7,221 in `gt200`) and **4,867 are re-drawn** (3,215 `absent` and 1,652 `home`); the remaining
  3,434 of the 8,301 re-drawn persons sit in the `10_25`/`25_50`/`50_100` classes
  (`on/state_diagnostics.json`, `by_assigned_class`). A worker stays at the workplace because the
  model re-draws only when the assigned class exceeds the donor's class and then keeps the person
  with `P(keep) = share_at_workplace(assigned) / share_at_workplace(donor)`, read from the
  committed MiD table: with the numerator 0.3135 (the `100_200` row, which `gt200` also reads
  because MiD top-codes at 200 km) the keep probability is **0.531 / 0.552 / 0.572 / 0.675** for
  donor classes `lt10` / `10_25` / `25_50` / `50_100`, and exactly **1.0** when a `gt200` worker's
  donor is already `100_200`. Two options would move the band further down, recorded with their
  DIRECTION only and with no number invented for either: (a) extend the `absent` rule to the
  `100_200` class instead of letting it keep the MiD value -- this would remove more far commuters
  from the reporting day and lower the band, at the cost of Assumption 3, which is deliberately
  conservative because MiD still measures 0.3135 of that class at the workplace; (b) lower the keep
  ratios -- this would lower the band across all classes at once, but the ratio is currently read
  straight from the committed MiD table by column name, so lowering it means introducing a factor
  with no committed source. Neither is adopted here. **The bound itself remains an unsourced
  pre-registered ASSUMPTION** (every `100_plus` column of the committed SrV table is exactly 0.0,
  a structural survey blind spot per ADR-0102 Assumption 2), so the "fail" is a failure against a
  chosen operating bound, not against an observed value.
- **The 100 % proof inside a scheduled production run stays OWED.** Ruling R4 substituted a 100 %
  proof on the CACHED i329 population for check 6's "25 % proof run first"; the proof in a
  scheduled production run has not happened and is not claimed. A manifest is to follow when it
  does.
- **The 37,705 workers with an assigned workplace whom the population does not flag employed are
  recorded as an OPEN DATA QUESTION and are not acted on here.** They are counted per Kreis
  (`n_workers_not_employed`) and warned about; why the model assigns workplaces to 12.4 % more
  persons than the enriched population calls employed is unexplained, and is tracked with the
  participation gap under issue #244.

**Rejected here, with the reason:**
- **Relaxing either bound post hoc.** Rejected: both are pre-registered in this record, both are
  explicitly labelled unsourced ASSUMPTIONS, and moving a threshold after seeing its own result
  destroys the only thing the pre-registration buys.
- **Switching the default to OFF.** Rejected: it would discard a demonstrated and side-effect-free
  improvement of the far-commuter tail (-2.63 pp on the reporting-day `100_plus` band, with the
  written population differing for exactly the 8,301 re-drawn persons and no other), on the
  strength of two failures against bounds that have no observed value behind them.
- **Re-specifying check 1 inside this record.** Rejected: see above -- it would be a check
  retro-fitted to its own outcome. It is named as follow-up work instead.

**Follow-ups named, none of them done here:** (i) the 100 % proof in a scheduled production run
(check 6); (ii) a re-specification of check 1 against a home-office attribution the population does
not yet carry; (iii) the fourth check-4 diagnostic, pool size per matching cell, which the
diagnostics dict still does not record; (iv) folding the reporting-day trips diagnostics (the R8/R9
counters, currently log-only) into a committed JSON in the next run; (v) the 37,705
not-employed workers and the participation gap, both under issue #244.

### Decision 2026-09-06 on check 2 (maintainer)

The maintainer reviewed the Proof 2026-09-06 result above and decided on the `100_plus` half of
check 2, the one item the proof itself left "reserved for the maintainer" (see "Check 2's 3 %
bound is not met..." above).

- **The pre-registered `100_plus < 3 %` bound is WITHDRAWN.** It was already labelled, at
  registration, as "a chosen operating bound, pre-registered a priori, with NO reference behind
  it" (Decision, check 2, above), because the committed SrV reference cannot supply one: every
  `100_plus` column of `srv2023_commute_distance_by_kreis.csv` is exactly **0.0** in all 15 rows
  (all three scopes, raw and shrunk) -- the survey records no work trip at or above 100 km at
  all. ADR-0102 Assumption 2 records why that zero is itself suspect rather than a true zero: a
  one-day regional survey plausibly UNDER-observes irregular long-distance commuting, and
  GIS-invalid SrV work trips (excluded from the reference by construction) carry a heavier
  long-distance tail than GIS-valid ones. A bound built on a reference that is a known structural
  blind spot cannot be defended as a scientific target, so it is withdrawn rather than kept as an
  unmet threshold. **No other bound in this ADR is touched**: check 1's +/- 3 pp band, check 2's
  own no-deterioration half, `cds_max_states_outside_employed_share` 0.05 and
  `commute_day_max_not_replaceable_share` 0.5 all stand exactly as pre-registered.
- **The reference for the long band is the REGIONAL MiD 2023 tables for the Grossraum
  Braunschweig (infas 7555), not the national-ratio consistency check tried first.** Those
  regional tables are committed and carry a regional, reporting-day (and register-like)
  reference that the withdrawn SrV bound could not supply, in two matched views:
  - **Reporting-day, work trips only:** `mid2023_W12_triplength_by_purpose.csv`, Tabelle A W12,
    row `Arbeit` (row %, of the day's Arbeit trips): `d_100km_plus` **1 %**, `d_50_100km`
    **2 %**, mean 15.2 km.
  - **Assigned/register view, persons by usual commute distance:** `mid2023_P13.csv`, Tabelle A
    P13, row `Gesamt` (n_unweighted 1,583, a register-like universe closer to the model's
    BA-anchored assignment than a reporting-day trip count): `d_100p` **2 %**, `d_50_100`
    **4 %**, mean 20.7 km (Kreis rows vary widely, e.g. Landkreis Goslar `d_100p` **10 %**).
- **Against these regional references the model still sits high, on both matched views** (all
  read from the committed proof artefacts of this same run, no new measurement):
  - Reporting-day, `on/commute_by_kreis.csv`: `zgb`/`all` row `model_share_100_plus`
    **0.038190** (3.8 % of all commuters) and `zgb`/`inter` row **0.073740** (7.4 % of
    inter-Gemeinde commuters) -- both against W12's `Arbeit` `d_100km_plus` of **1 %**.
  - Assigned/register view: of the 304,900 all-scope workers, **16,194** have an assigned class
    at or above 100 km (`on/state_diagnostics.json`'s `by_assigned_class`, `100_200` 5,235 +
    `gt200` 10,959), i.e. **0.053112** (5.3 % of all workers -- the same figure as the
    `zgb`/`all` row of `off/commute_by_kreis.csv` and of `on/commute_by_kreis_all_assigned.csv`)
    against P13's `Gesamt` `d_100p` of **2 %**. Of those 16,194, **11,327** (4,106 `100_200` +
    7,221 `gt200`) stay `at_workplace` on the reporting day -- the same 0.038190 figure above,
    read a second way: 11,327 / 296,599 = **0.0382**.
- **The comparison is read as four labelled findings, not folded into one verdict:**
  - **(a) The earlier national-ratio "MiD-implied" comparison is demoted: none of its three
    figures is a regional reference.** Computed by
    `scripts/report_mid_implied_reporting_day_bands.py` (issue #244) and committed as
    `.../mid_implied_reporting_day_bands.csv`, it applied MiD's NATIONAL `share_at_workplace`
    ratios (read by column name from `mid2023_workday_location_by_commute_distance.csv`, which
    carries no Grossraum Braunschweig geography at all) to the region's own BA-assigned
    distribution: `100_plus` assigned (flag OFF) **0.1001**, MiD-implied **0.0602**, model
    realised (flag ON) **0.0737** (and, one class down, `50_100` assigned 0.1330, MiD-implied
    0.1185, model ON 0.1304). It remains valid ONLY as an internal-consistency check of the
    model's OWN keep-probability rule against itself, not as an independent regional
    observation. Kept in that limited role: the model's 0.0737 sits **1.35 pp** above the
    MiD-implied 0.0602, read (Decision above, unchanged) as the necessary consequence of the
    "re-draw only upwards" rule -- the model never adds a work trip for a person whose donor
    commuted farther than their assignment -- not as a new finding.
  - **(b) The remaining factor of roughly 3-4 against the regional MiD tables lies in the
    far-commuter mass itself, not in a measurement artefact** (0.038190 vs W12's 1 % is ~3.8x on
    the reporting-day/all view; 0.073740 vs W12's 1 % is ~7.4x on the reporting-day/inter view;
    0.053112 vs P13's 2 % is ~2.7x on the assigned view). Two candidate causes are named, both
    PARAMETER/ANCHOR decisions, and NEITHER is taken here: (i) the register's >= 100 km
    commuters may be absent from the region on a given reporting day far more often than the
    national MiD keep ratio implies -- e.g. because they commute weekly rather than daily --
    which would argue for an `absent` share (or lower keep ratios) reaching into the `100_200`
    class and/or the classes below it, not only `gt200`; (ii) the BA-anchored far-commuter mass
    itself may be too high for this region (P13's assigned reference is 2 % against the model's
    assigned 5.3 %), which is the layer-1 question already re-targeted to issue #359 / ADR-0103
    (WHICH destination a commuter is sent to, not how the day state re-draws it). Choosing
    between (i), (ii), or a mixture of both is not decided here.
  - **(c) P38.2 contradicts P13 and is not usable as a bound until resolved.**
    `mid2023_P38_2_commute_distance_by_kreis.csv`'s `Gesamt` row gives `d_100_200km` 6 %,
    `d_200_300km` 4 %, `d_300km_plus` 3 % (~13 % >= 100 km, mean 66.1 km) against P13's 2 % --
    a factor of ~6 on what ADR-0102 already calls, without resolving, "Pendeldistanz tables"
    (i.e. the two tables' variable and universe are not established to be the same quantity).
    P38.2's `Gesamt` row also carries a 13 % `d_unplausibel_keine_angabe`
    (implausible-or-no-answer) share that its band percentages do not visibly exclude. This is
    recorded as an OPEN DATA QUESTION and is not adjudicated here: P38.2 is not used as a bound,
    above or below, until the variable/universe conflict with P13 is resolved.
  - **(d) Caveats of the W12/P13 regional tables, carried forward rather than smoothed over:**
    row percentages are rounded to the nearest whole percent, so the >= 100 km figures compared
    above are themselves within +/- 0.5 pp of their unrounded value; P13's `Gesamt` row rests on
    n_unweighted **1,583** persons; W12 measures a TRIP universe (`Arbeit` trips on the
    reporting day) while P13 measures a PERSON universe (usual commute distance), so the two are
    matched views of related but not identical quantities; and both tables' distances are
    self-reported or survey-imputed, not routed.
- **Check 2 verdict under the regional MiD reference: NOT MET.** The model's realised `100_plus`
  reporting-day share sits above the regional MiD reference by roughly a factor of 3-4 on every
  matched view (reporting-day 3.8 % / 7.4 % against W12's 1 %; assigned 5.3 % against P13's 2 %).
  **No numeric tolerance is asserted**: this is a comparison of point estimates against a table
  with the caveats named in (d) above, not a pre-registered band, so the factor is reported as a
  magnitude, not judged against a chosen threshold. The no-deterioration half of check 2 is
  **UNCHANGED** and stays **PASS**, exactly as recorded in the Proof 2026-09-06 section above
  (`commute_by_kreis_all_assigned.csv` byte-identical to the 2026-09-04 baseline).
- **Nothing else in the Proof 2026-09-06 section is amended by this decision.** Check 1 remains
  FAILED at its pre-registered +/- 3 pp band; the five follow-ups named there remain open and
  undone; no threshold anywhere else is relaxed. A SIXTH follow-up is added here: resolve which
  variable and universe `mid2023_P13.csv` and `mid2023_P38_2_commute_distance_by_kreis.csv` each
  measure (finding (c) above) before either is used as a bound, and decide between findings
  (b)(i) and (b)(ii) -- both parameter/anchor decisions reserved for the maintainer -- as the
  candidate cause of the remaining factor-3-4 gap.
