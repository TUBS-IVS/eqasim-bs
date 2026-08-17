# ADR-0033 · 2026-06-08 · SimWrapper dashboard export (Python emitter + Java contrib)
- **Status:** active
- **Context:** Run analytics should be viewable inside the MATSim/SimWrapper ecosystem, not only as
  the project's HTML dashboard.
- **Decision:** Two layers: (1) the MATSim simwrapper contrib behind `--simwrapper` (default off,
  byte-identical when off); (2) a Python emitter (`analysis/simwrapper/`) converting the existing
  dashboard `record` into SimWrapper CSV+YAML (8 chart/table tabs + 4 map tabs + a commuter tab),
  default-on inside full analysis and as a synpp stage writing only a new `simwrapper/` subfolder.
- **Rationale:** No scientific logic is duplicated (it reuses the existing dashboard `record` and
  spatial helpers); tabs whose source data is absent are skipped with an explicit log (no silent
  skip) (CLAUDE.md "SimWrapper dashboards").
- **Consequences:** Existing run outputs stay byte-identical; works in synthesis-only and full modes.
- **Superseded in part by ADR-0074 (2026-07-22):** layer (1)'s "default off" describes the state
  recorded here, when the Java contrib was not yet in the fork. ADR-0074 merged Layer 1
  (`eqasim-java-bs#12`) and set `simwrapper_dashboards` **default ON** under the feature-flag
  policy. The rest of this record (the two-layer split, the Python emitter, byte-identity when the
  flag is off) stands. Reading this line as the current default is the defect issue #253 reported.
- **Evidence:** plan `docs/superpowers/plans/2026-06-08-simwrapper-dashboard-export.md`;
  memory `project-simwrapper-dashboard`; PROJECT_STATUS.md §2.7.

