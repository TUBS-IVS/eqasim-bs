# ADR-0046 · 2026-06-24 · REJECTED — PopulationSim importance/expansion calibration framework
- **Status:** rejected (design only; recommend formal close)
- **Context:** Every popsim control carries a uniform importance 1000; the PopulationSim docs
  recommend iterative importance/expansion tuning. A coordinate-descent calibration framework was
  designed (with donor KPIs held out and a baseline-vs-tuned verdict).
- **Decision:** Do NOT build/activate the importance calibration; keep it parked as design only.
- **Rationale:** Measured on the 100% run, the controls are already hit (HH total exact, 11/43,598
  cells off, +0.022%), so importance tuning "would not help"; bumping importance instead makes the
  simultaneous integerizer THRASH (no completion at 3×/10×) or hit INFEASIBLE even at the doc's own
  1e9 recommendation; the residual 100m composition under-fit is donor-bound (rare/large HH types are
  thin in the MiD seed), so the real lever is the German MiD donor, not importance
  (PROJECT_BACKLOG.md step-1b/proof iteration).
- **Consequences:** The 19KB design+plan stay on disk unbuilt; the recommended lever is ADR-0038
  (German MiD donor, deferred).
- **Evidence:** spec `docs/superpowers/specs/2026-06-24-popsim-importance-calibration-design.md`;
  plan `2026-06-24-popsim-importance-calibration.md`; PROJECT_BACKLOG.md §1 (step-1b, nachsteuern
  proof) + Tier 5; commits `841fe05`, `2619fd1`, `d31c7eb`; memory `project-popsim-importance-calibration`.

