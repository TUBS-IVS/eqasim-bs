# ADR-0086 · 2026-08-17 · The Gemeinde EV tilt uses the combined electric share when the source publishes no BEV/PHEV split (issue #277)

- **Status:** active
- **Context:** ADR-0082 finding 3 moved the per-Gemeinde EV tilt from the KBA
  FZ 27.17 private-car sheet (Stichtag 2025-01-01, real `private_bev_share` /
  `private_phev_share` columns) to the newer per-Gemeinde EV time series
  (`kba_ev_gemeinde_timeseries_2023_2026.csv`, Stichtag 2026-04-01), taking both
  the numerator and the Kreis-mean denominator from that same 2026 file so the tilt
  is a pure within-Kreis relative factor. The tilt multiplies the `bev` and `phev`
  probability mass by `share(Gemeinde) / share(Kreis mean)`.
- **The defect:** in the 2026.04 edition of that export, **only the combined
  `Pkw Elektro Anteil` column carries information**. `Pkw BEV`,
  `Pkw Plug In Hybrid`, `Pkw_BEV_Anteil`, `Pkw Plug In Hybrid Anteil` and
  `Pkw Brennstoffzelle Anteil` are a literal `0` in every row of the period (and the
  absolute `Pkw Insgesamt` counts are 0 too). The extractor faithfully wrote those
  zeros into `kba_gemeinde_ev.csv` — 113/113 ZGB rows with `bev_share = phev_share
  = 0.0` while `ev_share` ranges from 2.5 % to 13.9 % — and the model read them as
  measured shares. With a zero numerator AND a zero Kreis denominator the tilt hit
  its `kreis_share <= 0.0` guard and did nothing, for every Gemeinde. The realised
  effect was measured after the derived tables were generated: the BEV probability
  for the most EV-rich ZGB Gemeinde (Tappenbeck, Kreis Gifhorn) was **identical**
  to the untilted Kreis value (0.02258 vs 0.02258), and the run log had been saying
  `powertrain Gemeinde tilt: primary 0/3000 (0.0%), fallback 3000 (100.0%)` all
  along — the instrumentation worked, nobody had run it on real data.
- **Decision:**
  1. **Zero is not a measurement when the whole column is zero.** A share column
     with no positive value in ANY row is treated as ABSENT and logged as a WARNING
     naming the columns; a zero INSIDE an otherwise informative column stays a
     measurement (a genuine no-EV pocket, which the documented 0.2 clip floor then
     handles). The screen applies only to the two columns that drive the tilt;
     `fuelcell_share` is stored as-is because hydrogen is never tilted.
  2. **Tilt on the combined electric share when the split is unavailable.** The
     numerator and denominator maps additionally carry the combined share under
     `COMBINED_ELECTRIC_KEY`, and `_apply_gemeinde_tilt` scales BOTH electric
     powertrains by that single factor. This preserves the Kreis-level BEV:PHEV
     ratio instead of inventing one, and keeps the spatial signal the source
     actually publishes.
  3. **A key match that tilts nothing counts as a FALLBACK, not a primary hit.**
     Previously a Gemeinde present in the map incremented the primary counter even
     when every per-powertrain factor was skipped — which is why a 100 %-inert tilt
     could still have reported matches. Tilts applied via the combined share are
     counted and logged separately.
- **Rationale:** the alternative to a combined-share tilt is to synthesise a
  BEV:PHEV split for each Gemeinde, which no source supports — that would be an
  invented reference value. The combined share, by contrast, is measured, complete
  (113/113 ZGB Gemeinden) and current (2026.04), and it carries exactly the signal
  the tilt exists for: WHERE electric cars are, not which kind. Keeping the
  within-Kreis BEV:PHEV ratio from the Kreis marginal is the honest treatment of
  what the data does not say. On the instrumentation side, the project's
  no-silent-fallback rule requires the fallback rate to be meaningful; a counter
  that treats "matched but did nothing" as success violates it.
- **Consequences:** the Gemeinde tilt is active again for every ZGB Gemeinde
  (Tappenbeck: BEV pmf 0.0226 → 0.0487 at a combined-share ratio of 13.9 % vs the
  6.2 % Kreis mean, clipped tilt factor 2.24). Fleets change accordingly, and the
  ADR-0085 per-Kreis rake preserves the Kreis aggregate while the tilt redistributes
  within it. If a future KBA edition restores the BEV/PHEV columns, the
  per-powertrain path takes precedence automatically and the log line reporting
  combined-share tilts disappears — no config change needed. FZ 27.17 remains the
  weight source for the Kreis-mean denominator.
- **Evidence:** `tests/test_fleet_gemeinde_ev_2026.py` (all green, including
  `test_zero_fuelcell_not_dropped`, which pins that a measured zero in a
  non-tilting column survives);
  `tests/test_fleet_sampling_de.py::test_gemeinde_tilt_raises_local_bev_share` is
  green again — it had been failing with base == tilted == 0.0365;
  the raw-column degeneracy is reproducible from
  `eqasim-data/data/braunschweig/kba/raw/kba_ev_gemeinde_timeseries_2023_2026.csv`
  (`Pkw_BEV_Anteil` has exactly one distinct value, `'0'`, across all 2026.04 rows).
- **Alternatives rejected:** (a) Revert to FZ 27.17 (2025) for the whole tilt — it
  has a real split but is a year older, covers private cars only, and its Gemeinde
  coverage is the same 113 rows; the combined 2026 share is the better numerator and
  FZ 27.17 is still used for the denominator weights. (b) Split the combined share
  into BEV/PHEV using the national or per-Kreis ratio — arithmetically easy and
  scientifically indefensible: it would present a modelled split as a
  per-Gemeinde measurement. (c) Raise instead of falling back to the combined share
  — the combined share is a valid, sufficient basis for a spatial tilt, so failing
  the run would reject usable data. (d) Keep the zeros and let the tilt stay inert —
  the status quo the merge found, and the reason this record exists.
- **Issue / PR:** #277
