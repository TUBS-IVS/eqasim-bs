# ADR-0030 · 2026-06-11 · Long-haul freight injection (german-wide-freight v3, hybrid Java→Python→Java)
- **Status:** active
- **Context:** Heavy-goods through-traffic on the ZGB motorways (A2/A7/A39) is not represented;
  correctly classifying TRANSIT vs INTERNAL/INCOMING/OUTGOING requires routing each freight trip on
  the German-wide network (a straight-line OD test would miss exactly the through-traffic).
- **Decision:** Inject long-haul road freight from the VSP german-wide-freight v3 model via a
  three-stage hybrid: (1) the published matsim `RunExtractFreightTrips` Java tool, run once per
  category (cached, 100%, sampling-rate independent); (2) a Python trips stage parsing the plans;
  (3) a Java `RunInjectFreight` hook after the cordon cut, Bernoulli-sampled to the run's sampling
  rate. Flag `freight_enabled` (code default true; OFF byte-identical). Freight agents are isolated
  from mode choice and excluded from all person-travel analysis.
- **Rationale:** The published, peer-reviewed Java tool routes+classifies+trims correctly; the build
  writes no category attribute, so the unmodified tool is run once per `--tripType` (verified on the
  real output: all 49,758 trips came back `unknown`). Freight sampling is required because the qsim
  flowCapacityFactor is scaled to the sampling rate (CLAUDE.md "Long-haul freight injection").
- **Consequences:** `freight_truck_pce=3.5` and `_max_velocity_kmh=80` are explicit ASSUMPTIONS
  (StVO / uncalibrated); a BASt HGV-count calibration is a parked follow-up (ADR-0034 / Tier 3.2).
- **Evidence:** plan `docs/superpowers/plans/2026-06-11-german-wide-freight-injection.md`;
  `docs/features/freight.md`; memory `project-freight-injection`; PROJECT_STATUS.md §2.6.

---

## Analysis / dashboards

