# Project Backlog & Open-Work Map — eqasim-bs

Historical feed: `docs/archive/BACKLOG_HISTORY.md`

> **Purpose.** A ranked, honest inventory of everything we *planned* but only *partially*
> implemented, deliberately parked, or designed-and-forgot — cross-checked against what the
> code already does (so superseded ideas are marked dead, not re-attempted).
>
> Created: 2026-06-27. Companion to `docs/ONBOARDING.md` (narrative), `docs/DECISIONS.md`
> (the *why*), and `CLAUDE.md` (binding instructions). Where they disagree, `CLAUDE.md` and
> git win — this is a point-in-time snapshot.
>
> Sources: 50 Claude memory files, 14 design specs in `docs/superpowers/specs/`, git
> branch/merge analysis, `SESSION_LOG.md`, direct code inspection.

---

## 0. TL;DR — the live state in five sentences

1. **Almost everything we built is merged to `main`** (synthesis, fleet, cordon einpendler,
   freight, education gravity, income tilt, simwrapper, building-activity-potentials via
   PR #16/#17). The model is in good shape and validated at synthesis level.
2. **Most of the Calibration Corner is ALREADY on `main` via PR #18** (`a627fca`). The
   genuine remainder sits on `reconcile/calibration-remainder` (Tier 0.1) — conflict-resolved,
   pending a server test run + push.
3. **Two genuinely planned-but-never-built designs** sit on disk: PopulationSim *importance*
   calibration (zero code) and one half of the gravity distance-distribution calibration.
4. **The "produce results" front is the real bottleneck**, not features: no 100% production
   run exists on the newest code, and mode choice is still OFF (no calibrated modal split).
5. **The German MiD Wege trip donor is DONE** (merged via `feature/population-method-workflows`,
   issue #24 closed 2026-06-29). The *narrower* lever that remains is **seed-donor diversity**
   (the ZENSUS100m household composition is donor-bound — rare/large household types are thin
   in the MiD seed; see §2.1).

See `docs/archive/BACKLOG_HISTORY.md` for the full dated history (2026-06-28 through
2026-07-19). Highlights since this TL;DR was written: #140 cross-cordon student in-commuters
BUILT (1% cordon dry-run still pending); #124 phase 1 + #132 measured (PR #189/#190); the
`in_ausbildung` control shipped (PR #173); #96/#97 population-validation fixes cleared the
Phase-0 gate for #99; #156 MATSim output archive and #129 in-commuter mode reference merged
(PR #180/#181).

---

## 1. Priority ranking (what to do, in order)

Ranked by **(value × readiness) ÷ cost**, with loss-risk items pulled to the top. Full
pre-trim text (effort/status columns, commit hashes, branch names) is preserved verbatim in
`docs/archive/BACKLOG_HISTORY.md` (Appendix).

### Agreed next sequence (user-directed, 2026-06-27)

1. **popsim — verify configuration, then test the weights.** *(FIRST — foundation.)* Step 1a
   (config audit vs the official PopulationSim reference) + step 1b (importance/expansion
   measurement vs Zensus controls) done — full audit + verdict in the archive.
2. **Calibrate the gravity model** *(AFTER popsim)* — make `calibrate_gravity_distribution.py`
   popsim_mid-compatible, then run on `cache_bs_100pct_allfeat_synth` to pin per-band friction.
3. **Real monetary costs (PT/car) at the end** *(LAST)* — VRB tariff + car costs feeding the
   mode-choice utility; needs a committed VRB tariff reference (no invented fares).

### TIER 0 — Do now (cheap, high urgency, prevents loss / unblocks everything)

- **[0.1]** Calibration-corner remainder — bulk already on `main` (PR #18/#19); remainder on
  `reconcile/calibration-remainder` — run the server test suite, then push as one PR.
- **[0.2]** DONE (2026-06-27) — stale partial edit in `distance_distributions.py` reverted.
- **[0.3]** DONE (2026-06-27) — 3 superseded prototype branches deleted local+origin (verified
  redundant, see archive §5). Retire `feature/calibration-corner` + its worktree once 0.1 lands.
- **[0.4]** DONE — `integration/all-features` is merged into `main` (verified 2026-08-09).
- **[0.5]** **Test evidence is stale and unautomated** (found 2026-08-09). `.github/workflows/tests.yml`
  triggers on `develop`, which does not exist in this fork, so the suite has never run in CI; the last
  recorded green (2026-07-19, felix, 3170 passed) predates 59 commits / 12 merged PRs on `main`.
  Fix the trigger (`develop` -> `main`), then re-run the full suite on felix against `8ee06c0`.
- **[0.6]** Remote branch hygiene: 17 fully-merged `origin/*` branches are deletable, 7 more are
  feature-superseded backups (full classification in `PROJECT_STATUS.md` §3). Needs push approval.
  Genuinely open work in that list: `feature/escort-purpose-201` (#201, 18 TDD commits, never PR'd).

### TIER 1 — High value, mostly ready (the "produce defensible results" front)

- **[1.1]** 100% server production run on newest code (popsim_mid + fleet + cordon + freight +
  building potentials). Tier-A/B caching built to make this affordable; mostly compute.
  `matsim.output` is now e2e-green on the eqasim-java 2.2.0 stack at 1-Kreis + freight (2026-07-23,
  PR #239, ADR-0071), so the full-scale run is unblocked on the new stack.
- **[1.2]** Mode-choice ASC calibration (turn DMC on, anchor modal split to the committed
  `mid_mode_margin_by_bundesland.csv`). DEFERRED on purpose; needs the Java-side ASC loop.
- **[1.3]** Finish Tier-A/B cache config wiring (`cache_share_stages` list + fixed
  `popsim_work_dir` in server configs); verify the completed_donor byte-identity gate test.
- **[1.4]** #148 KREIS household-control apportionment — PR #176 MERGED (ADR-0062: apportion by
  household share, not population share; ~5.9% mis-apportionment fixed). Genuinely open follow-up
  (verified 2026-07-22, not a separate issue by choice): a small resolved-Kreis + household-share
  A/B rerun on felix to validate the realised within-Kreis effect — still not run.
- **[1.5]** Fleet-quality realism upgrade — branch `feature/fleet-quality-and-data` (ADR-0051)
  pushed, unmerged. Needs the server phase (KBA/MiD extraction scripts, canonical pytest, 1% smoke,
  regenerate 2 stale OFF goldens) then `git pr` merge.

### TIER 2 — Real model improvements, partial or designed (assess value before building)

- **[2.1]** Seed-donor diversity (richer MiD seed for ZENSUS100m household composition) —
  donor-bound per the popsim audit; needs a bigger MiD microdata sample (SUF). New issue to scope.
- ~~**[2.2]**~~ DONE — LoD2 height → volume-weighted dwelling capacity & typing (verified
  2026-06-27).
- ~~**[2.3]**~~ DONE — real building-worker dataset replaces the area\*floors proxy (verified
  2026-06-27, `work_building_potentials=True` default).
- **[2.4]** Education distance-distribution calibration (Phase 2 of the gravity plan).
  PLANNED-ONLY; build only if education trip-length validation shows a gap — measure first.
- **[2.5]** **#78** — secondary scorer scale-alignment calibration (measure-first follow-up; PR #77
  / #27 shipped the infra only). Run `calibrate_secondary_scorer.py` on `cache_bs_25pct_allfeat`;
  pin `attr_transform`/weights only on a measured win vs the OFF baseline; do not raise `pot_weight`.
- **[2.6]** **#240** — car/bike ownership control-fit: MiD-informed 1 km disaggregation of the KREIS
  SrV control. Analyzed + recommended (control-fit dashboard 2026-07-23: car/bike ~3.6-4.7pp on
  category shares, urban-concentrated, BS-Stadt −3.0pp; root cause = coarse KREIS geography + coupled
  1-person/renter/low-income segment, NOT importance). Not yet built. Data confirmed present
  (`mid2023_cars_by_raumtyp`, `cars_by_status_hhtype`, H7/H12). Design: estimate 1 km cell shape from
  MiD, then IPF/rake to the KREIS anchor. Measure-first per the calibration discipline.
- **[2.7]** **SrV 2023 BS+RGB validation wave (#241-#250)** — 10 issues opened 2026-07-24, not yet
  ranked here. Mostly *validation references* rather than new model levers: #241 W_ZWECK 13-16/99
  silent-fillna purpose mapping (a real fallback-transparency bug — pull forward), #242 W_ZWD subtypes,
  #244 home-office frequency, #245 start/destination AGS as OD reference, #246 mode-detail pack
  (car occupancy, joint travel, e-bike), #247 home-to-PT-stop walk times, #248 regional fleet
  realism (EV counts, drive types, mileage), #249 parking calibration, #250 B2C delivery demand.
  Also open and unranked: #226 (SrV as regional HTS chain donor), #227, #228, #201 escort purpose
  (branch exists, see [0.6]), #203, #123, #117, #138, #141.

### TIER 3 — Deferred-deliberately / future waves (parked with intent, not forgotten)

- **[3.1]** Kreis-level income control as a PopulationSim control. **#108 L1 MERGED** (PR #112,
  economic_status × Kreis); **L2 MERGED** (PR #212, ADR-0069: own-donor income +
  signature-preserving reallocation; 2-Kreis gate passed).
  Open: G1/G2 sub-Kreis measurement to gate L3/#110. Spec `docs/superpowers/specs/2026-07-04-
  income-weighted-household-placement-design.md`.
- **[3.2]** BASt Dauerzählstellen HGV-count calibration for the injected freight. Future
  external-validation wave.
- **[3.3]** Real VRB/DELFI GTFS + VRB PT tariff (B2) + MATSim termination/iteration tuning.
- **[3.4]** Cordon sub-projects 3 & 4 (external visitors, non-freight through-traffic). Never
  started; out of scope in the original roadmap.
- **[3.5]** HSN/TSN → engine power/Fahrleistung/CO2 (HBEFA) wiring; economic_status ×
  #earners margin. Parked ("income/socio saturated").

### TIER 4 — Polish / nice-to-have

- **[4.1]** SimWrapper polish: verify choropleth colours, anglicise residual German labels,
  wire into 25/100% configs, one full 1% MATSim+fleet run to validate all 13 tabs.
- **[4.2]** PopulationSim `num_workers` tuning on the 64-core server; education sparse `cdist`.
  Perf only, no OOM risk, deferred.
- **[4.3]** ~~Config cleanup (**#81**)~~ **RESOLVED-IN-REVIEW — [PR #234](https://github.com/TUBS-IVS/eqasim-bs/pull/234)** (closes #81/#230): 37 root `config_*.yml` -> composed `configs/base_bs.yml` + per-scale overlays; 9 fixtures -> `configs/fixtures/`, 15 ballast `git rm`'d, root config-free. **MERGED 2026-07-22** (closed #81/#230). ADR-0070.
- **[4.4]** Factor a reusable 1km-cell control-fit smoke test (planned in the PR #173 /
  `in_ausbildung` control spec, not yet built) — no issue yet.

### TIER 5 — Drop / do NOT re-attempt (recorded so we don't loop back)

Tried or designed and **deliberately killed** — the model already does better, or measurement
showed no gain. Full reasoning per row is preserved verbatim in the archive appendix.

| Killed idea | Why dead (short) |
|-------------|----------|
| PopulationSim *importance/expansion* calibration framework (19 KB spec, zero code) | Controls already validate well; coordinate-descent risks overfitting survey noise. Recommend formally closing (§3). |
| Commute gravity friction pinning | Commute already matches MiD P13; old "0.47 FAIL" was stale. Kept as gated-off infra. |
| Distance-dependent detour curve f(d) as default | Measured immaterial vs constant 1.3. Constant stays default. |
| Secondary scorer `pot_weight` tuning | `pot_weight` is a concentration knob; raising it makes the fit worse. Default 1.0 optimal. |
| Raking employment to MiD P9 | P9 is survey noise. Employment stays raked to GENESIS 13111. |
| Within-Kreis *extra* income signal | No external sub-Kreis ground truth exists; size/tenure/age already dominate. |
| ATTACH strategy for building potentials | Replaced by REPLACE (gpkg buildings as candidate set). |
| Sector-aware work-attraction tilt (#128, ADR-0065) | Measured: per-Gemeinde inflow fit 9x worse vs OBSERVED SvB. Code stays gated-off infra. |
| HTS-matching step 1 for aggregate purpose fit | Improves coherence but SRMSE is donor-pool-bound (→ 2.1, not step 1). |

---

## 2. Status of every design spec (`docs/superpowers/specs/`)

| Spec | Status | Open part |
|------|--------|-----------|
| per-RegioStaR-7 gravity slope (06-01) | IMPLEMENTED | — |
| cordon external-demand roadmap (06-02) | PARTIAL | sub-projects 3-4 not started (Tier 3.4) |
| supply extension cordon ring (06-02) | IMPLEMENTED | — |
| in-commuter agents v1 / v1.1 (06-02) | IMPLEMENTED | extended analysis only on stale branch |
| education gravity (06-03) | IMPLEMENTED | — |
| incommuter mode reference (06-03) | IMPLEMENTED | — |
| age-aware household chunking (06-04) | IMPLEMENTED | — |
| cross-cordon external demand (06-05) | IMPLEMENTED | — |
| fleet KBA/MiD (06-07) | IMPLEMENTED | emissions wiring parked (Tier 3.5) |
| population validation (06-07) | IMPLEMENTED | — |
| Tier-A attribute reactivation (06-07) | IMPLEMENTED | — |
| ALKIS-typed home matching (06-17) | IMPLEMENTED (PR #14) | — |
| LoD2 height-volume capacity (06-17) | IMPLEMENTED | — |
| fleet consistency + income-age (06-18) | IMPLEMENTED (PR #12/#13) | — |
| weekend-plan match (06-18) | IMPLEMENTED | — |
| shared stage-cache (06-22) | IMPLEMENTED | — |
| Tier A+B caching (06-22) | PARTIAL | config wiring (Tier 1.3) |
| auto-export shared cache (06-23) | IMPLEMENTED | — |
| integerizer-quality analysis (06-23) | IMPLEMENTED | — |
| popsim importance calibration (06-24) | PLANNED-ONLY | entire framework (Tier 5 / §3) |
| building-activity-potentials (06-25) | IMPLEMENTED (PR #16/#17) | — |
| calibration corner + distance-dist (06-25) | PARTIAL | lives on worktree branch (Tier 0.1) |

---

## 3. Open decisions for the user (genuine forks)

1. **Calibration-Corner worktree (Tier 0.1):** merge the whole 68-commit body as one PR, or
   cherry-pick only the *kept* parts and leave the measured-immaterial infra gated-off?
2. **PopulationSim importance calibration (Tier 5):** formally close the 19 KB design, or
   keep it parked as an "if a control regresses" fallback? (Recommendation: close — it risks
   overfitting noise we deliberately don't rake to.)
3. **Highest next lever:** German MiD Wege donor seed diversity (2.1, big, blocked on data)
   vs. 100% run + mode-choice calibration (1.1/1.2, ready)?
4. **Remote branch deletion** — 17 merged + 7 feature-superseded `origin/*` branches, classified
   in `PROJECT_STATUS.md` §3. Deleting them is a push, so it needs your explicit go-ahead.
5. **`feature/escort-purpose-201`** (18 TDD commits, newer than `main`) — finish and PR, or park
   with a status? It is currently the only unmerged work that is *newer* than `main`.

---

## 4. Standing lessons that shape all of the above (do not violate)

- **Measure before calibrating.** The commute "0.47 FAIL" was stale; the model already
  matched P13. Always re-measure the realised KPI with the *same* methodology as the target
  and decompose targets→intermediate→realised before building a calibration lever.
- **No invented references; convergence ≠ validation.** A target is only real if traceable
  to a committed source; otherwise label it ASSUMPTION.
- **No silent fallbacks.** Log primary-vs-fallback rate; test the primary path.
- **Anti-overfitting.** Survey noise (P9, sub-Kreis income) is not a calibration target.
- **New flags default ON, OFF path byte-identical + tested. Never push without explicit OK.**
- **Parallel agents → separate worktrees** (we have had HEAD-race incidents).
