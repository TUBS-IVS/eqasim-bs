# ADR-0074 — MATSim contribs in lockstep via `${matsim.version}`; property bumps are deliberate upgrade rounds; SimWrapper Layer-1 merged with `simwrapper_dashboards` default ON (2026-07-22, java-bs#12 + PR #233/#236; carried over post-#260 from `backup/pm-0d117f7`, where it was numbered ADR-0071 before colliding with origin's 0071)

- **Context:** issue #215 found `--simwrapper` inert on eqasim-java-bs main (the Java Layer-1 lived
  only on an unmerged pre-2.2.0 branch). Porting it exposed a second, worse problem: the braunschweig
  pom hard-pinned `org.matsim.contrib:application` to `2025.0-PR3568` while the parent
  `matsim.version` was `2026.0-2026w12` — the stale pin kept the build green and thereby HID that
  MATSim 2026 moved `ExtractRelevantFreightTrips` to
  `org.matsim.application.prepare.longDistanceFreightGER.tripExtraction` and renamed its CLI
  (`--legMode`, `--geographicalTripType`, new `--subpopulation` defaulting to `longDistanceFreight`
  instead of hard-coded `freight`).
- **Decision:** (1) all `org.matsim.contrib` dependencies reference `${matsim.version}` — never a
  literal copy of its value (artifact existence verified on repo.matsim.org before commit);
  (2) any bump of `matsim.version` (incl. dependabot, e.g. java-bs#8 → 2027.0) is a deliberate
  upgrade round with package/CLI verification, never an auto-merge; (3) the pipeline passes
  `--subpopulation freight` explicitly (downstream merge/replanning/freight_filter/analysis key on
  `freight`); (4) per the feature-flag policy, `simwrapper_dashboards` defaults ON (#236) —
  SimWrapperModule is analysis-only, `false` restores a byte-identical output directory.
- **Consequences:** the jar change invalidates the freight-extraction synpp cache → one-time
  re-extraction (~4×45 min); freshly extracted plans are NOT guaranteed byte-identical to the
  2025-build outputs (upstream tool changed: person tagging, internal refactor) — treat pre/post
  freight comparisons accordingly. Every MATSim run now writes SimWrapper dashboards into its
  output directory; first real SimWrapperModule execution is still pending (build-verified only).
  Verification recorded in PR java-bs#12 (full reactor exit 0, braunschweig tests 4/4, `--help`
  CLI contract) and PR #233 (freight suites 19/19).

