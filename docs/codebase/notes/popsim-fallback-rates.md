# PopulationSim fallback-rate logging

The popsim stage family (`braunschweig/popsim/`) logs every fallback site through ONE helper,
`braunschweig/popsim/fallback_rates.py :: log_fallback_rate`, so the format and the warn threshold
live in one place (CLAUDE.md "Fallback transparency"; audit package D, 2026-11-12).

## The rule

- A popsim site with a fallback counts PRIMARY vs FALLBACK items and calls `log_fallback_rate`
  with its own `[stage]` tag. The line reads `"[stage] primary P/N (p%), fallback F (f%)"`.
- Above `FALLBACK_WARN_THRESHOLD` the line is a WARNING. The threshold is one module constant,
  not a config key (owner decision 2026-11-12). It is an ASSUMPTION: a review trigger, not a
  measured tolerance.
- Items to which the method does not apply are counted as neither primary nor fallback
  ([fallback-counter-states.md](fallback-counter-states.md)).
- Raise instead of warn only where a sibling site already raises or the state is impossible
  (owner decision 2026-11-12). A known defect that is only counted names its audit finding in
  the `what=` text and is proposed as a GitHub issue to the owner (audit finding id in the `what=` text).

## Cache consequence

`fallback_rates.py` declares a `_SYNPP_TOKEN_EXEMPTION` (it never changes a stage result), which
`scripts/audit_synpp_helper_hash.py` honours. The own-package gate in
`tests/test_synpp_helper_hash_invariant.py` does not read exemptions, so a popsim STAGE module
(one with `configure` and `execute`) that imports the helper needs an `ALLOWED_VIOLATIONS` entry
there. Adding a log line still edits the calling module and re-executes every stage that hashes it.
