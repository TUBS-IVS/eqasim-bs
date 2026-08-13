# ADR-0057 — Secondary distance distributions rescue MiD coded-time Wege from `wegmin_imp1` instead of dropping them

- **Status:** accepted 2026-07-12 (PR #165, commit `f212d73`; Closes #160). Changes scientific outputs.
- **Context:** `braunschweig/popsim/distance_distributions.py` built the empirical secondary distance /
  travel-time distributions (consumed by `CustomDistanceSampler` for all shop/leisure/other/education
  secondary-activity placement) from MiD Wege clock times `W_SZS/W_SZM/W_AZS/W_AZM`. Those columns carry
  the MiD design codes 99 ("keine Angabe", ~1%) and 701 ("bei regelmaessigen beruflichen Wegen nicht
  erhoben" — rbW summary records of regular commuters, ~10%). `mid_time_seconds` NaN'd `travel_time` for
  these rows and the `travel_time >= 0` filter then silently removed all of them BEFORE any logging — a
  ~11% loss that is NOT missing-at-random (systematically commuters), biasing the distributions away from
  the commuter travel profile. This is the same MiD-code pathology for which the sibling consumer
  `trips.py` already has the dedicated `time_imputation.py` Stage-A cascade; that fix had never been
  ported to this second, independent consumer.
- **Decision:** reconstruct `travel_time` for coded-time rows from `wegmin_imp1` (MiD's own imputed
  per-trip duration in minutes; `* 60` -> seconds), the exact same primary source Stage A trusts for a
  trip's OWN duration. Only rows whose `wegmin_imp1` is itself coded/missing
  (`>= WEGMIN_CODE_THRESHOLD`, reused from `time_imputation.py`) are dropped. An explicit
  observed / imputed / dropped rate is logged every run, with a `CODED_TIME_DROP_WARN_RATE = 2%`
  escalation (per the mandatory no-silent-fallback rule).
- **Rejected alternative:** reusing the full `time_imputation.impute_chain_times` cascade — it
  reconstructs whole per-person day schedules (RNG-seeded anchor/duration pools) because `trips.py` needs
  valid absolute clock times; this aggregate stage only needs a DURATION as a binning key, so the
  lighter, fully-deterministic `wegmin_imp1` reuse is preferred (no RNG, no chain machinery).
- **Consequence:** future `popsim_mid` runs using the by-purpose / shop / leisure / other secondary
  distance distributions change quantitatively (commuter-relevant trips now included). The running kreis5
  100% run produced its secondary-distance stage with the old code — a re-run decision is tracked in
  PROJECT_BACKLOG.md. Part of the same wave: **#161** (fleet Gemeinde-tilt name normalization),
  **#162** (canonical `EMPLOYED_TAET` in weekend matching), **#163** (14 fallback-transparency items) —
  those are bug/instrumentation fixes, not separate architectural decisions.
- **Evidence:** PR #165; commit `f212d73`; `braunschweig/popsim/distance_distributions.py` Step 3b +
  module docstring; `braunschweig/popsim/time_imputation.py`; memory `project-audit-wave-2026-07-12`.

---

