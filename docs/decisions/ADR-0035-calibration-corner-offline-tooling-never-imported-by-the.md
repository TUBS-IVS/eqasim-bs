# ADR-0035 · 2026-06-25 · Calibration corner (offline tooling, never imported by the runtime)
- **Status:** active
- **Context:** Several offline calibrators (gravity per-RS7 slope, gravity decay, education slopes)
  and new distribution-calibration loops needed a single, clearly separated home.
- **Decision:** Create `braunschweig/calibration/` as the single home for offline calibration:
  shared metrics (`band_shares`/`emd_on_bands`/`apply_detour`), MiD distribution targets (P13/T43/W12
  loaders), per-model loops + CLIs + reports. It consumes runtime components and emits pinned YAML;
  it is never imported by the runtime pipeline. The three legacy calibrators are migrated in as
  `_legacy_*` with thin `scripts/calibrate_*.py` shims preserving behaviour.
- **Rationale:** Runtime model components stay with the model (per-band friction in `gravity/friction.py`,
  the chainsolvers scorer in its own stage); the corner holds only the offline loops, keeping
  simulation setup separate from analysis (`docs/features/calibration-corner.md`).
- **Consequences:** A clean place to build (and measure) calibrations before pinning; per-band
  commute friction wired into the model but defaulting to `None` (legacy `exp(slope·d)`).
- **Evidence:** PR #18 (merged 2026-06-26); `docs/features/calibration-corner.md`;
  `tests/test_calibration_migration_shims.py`; PROJECT_STATUS.md §2.4.

---

## Infrastructure

