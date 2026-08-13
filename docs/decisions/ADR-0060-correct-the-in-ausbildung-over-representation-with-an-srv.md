# ADR-0060 — Correct the in_ausbildung over-representation with an SrV+MiD per-Kreis employment_status control (14+)

- **Status:** accepted 2026-07-13/14. PR #173 (Closes #172, MERGED). Changes scientific outputs (flag default-on;
  OFF byte-identical). Follows ADR-0058 (which measured the attribute and deferred a Phase-1 control); this
  ADR builds a control for the ONE class that materially deviated (in_ausbildung), not the whole taxonomy.
- **Context:** synthetic `employment_status = in_ausbildung` is ~1.9x over-represented (3.6% vs the regional
  truth ~1.9%). Root cause (issue #172): NOT an age-structure or reference artifact — two independent regional
  references agree (MiD P9 1.93% and the newly extracted **SrV V_ERW=8** 1.87%). It is a two-stage
  compositional inflation of Azubis among young employed persons: MiD survey 24% -> completed-donor SEED 32%
  (member completion) -> balanced 40% (balancer up-weighting). Because `employment_status` was not a control,
  it floated free of the regional evidence.
- **Decision:** register `employment_status` as a per-Kreis soft PopulationSim control raked to a blended
  MiD-P9 + SrV-V_ERW target (`target2026_employment_status_by_kreis.csv`, via `blend_kreis_target` with
  Dirichlet shrinkage for the thin per-Kreis Azubi cells). The SrV `V_ERW` variable (codeplan
  `SrV2023_Datenkodierung_SciUse.xlsx`) cleanly separates Schueler(6)/Student(7)/**In Ausbildung(8)**; only
  V_ERW=8 maps to `in_ausbildung`, apples-to-apples with the P_BKAT-derived seed. The control uses a **14+
  age universe on BOTH halves** (an `& (persons.HP_ALTER >= 14)` clause in the seed expression AND a
  14+ per-Kreis total, `person_total_by_kreis_min_age`) so target and realized share the P9/SrV "ab 14 Jahre"
  base — avoiding the #97-class universe mismatch. `employment_status` is derived onto the popsim seed in both
  seed paths (load_mid_seed + project_completed_seed), mirroring trip_class.
- **Consequence / honesty note:** making `employment_status` a steering control means its
  population_validation control is now `independence="partially_independent"` (its P9 target is one input to
  the steering blend), NOT `independent` — corrected per the MANDATORY "convergence != independent validation"
  rule (final-review finding, commit 453cd59). The canonical in_ausbildung re-measure drops out of the next
  full "everything on main" run.
- **Rejected alternatives:** (a) fixing member completion alone (the balancer half would remain; a control
  pins the output regardless); (b) an SrV PT-subscription (`V_OEV_FK`) control — it is a usage-conditional
  ticket-TYPE, not a population Abo-ownership rate, and MiD is the better source; (c) SrV migration (`V_MIGR`)
  — a separate feature, dropped.
- **Evidence:** PR #173; 1-Kreis popsim smoke (Braunschweig, 8 workers): control rakes in_ausbildung
  **2.98% (unraked kreis5) -> 2.09%** (target 2.01%); commits 400c344..453cd59; spec/plan under
  `docs/superpowers/`; memory `project-employment-status-and-pbkat-bugs`, `feedback-popsim-smoke-scoping`.

