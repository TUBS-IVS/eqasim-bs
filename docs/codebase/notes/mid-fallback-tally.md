# MiD fallback tally: how the data-loader coupling tables report their fallbacks

`braunschweig.data.mid.raumtyp_tilt.FallbackTally` is the one counter the MiD coupling
tables (`income_by_size`, `income_by_status`, `cars_by_status`, `tenure_by_income`) use to
satisfy CLAUDE.md "Fallback transparency". The rule itself (what counts as primary,
fallback, not applicable) is `fallback-counter-states.md`; this note is only the
mechanism's contract. `status_by_hhtype` keeps its own small local tally for the same
purpose (no new import edge).

## Contract

- **Module-level state.** Each module owns one `_TALLY`; the table functions
  (`check`, `aux_check`, `unit`) write to it on every call, whoever the caller is.
- **Reset at stage start.** A stage that reports calls the module's `reset_fallback_rates()`
  first, so counts from other callers (tests, other stages) are dropped.
- **Report at stage end.** The same stage calls `report_fallback_rates()` once; it logs one
  line per build and resets. The reporting stages are the enriched-population stages
  `income_distribution`, `economic_status` and `housing_tenure`.
- **A caller that never reports loses its counts.** They stay in the module tally until the
  next reset. Any new caller of these tables must either report or accept being silent.
- **One unit name per denominator.** `aux_check(unit_name, ...)` counts one unit per call
  under `unit_name`. Two call sites that look at the same item must use different names,
  otherwise the item counts twice and the rate is diluted (the tenure bracket pmf and
  conditional used to share one name).
- **Counting never changes a returned value.** `check` and `aux_check` return their
  `fired` argument unchanged.
- **Warning level** is `FALLBACK_WARN_FRACTION` in the same module.
