# ADR-0019 · 2026-06-07 · Household vehicle fleet (vs eqasim default car)
- **Status:** active
- **Context:** The inherited path gives every car owner a generic default car; a realistic German
  fleet (segments, brands, powertrains, engine attributes) is needed for emissions/realism.
- **Decision:** Build a per-household fleet (`vehicles_method: household`) grounded in MiD H7 and
  KBA registration data, with a German segment+brand mix (`fleet_model_enabled`/`_brands`, KBA FZ),
  BEV/electric calibration (`fleet_electric_calibration`, KBA FZ 27.15/27.17), and HSN/TSN engine
  attributes (kW/ccm/fuel, `fleet_hsn_tsn_attributes`).
- **Rationale:** Each layer is grounded in a committed KBA/MiD reference (spec
  `2026-06-07-fleet-kba-mid-design.md`); all flag-gated.
- **Consequences:** Enables fleet-level analysis (brand/powertrain maps); emissions wiring
  (HBEFA consumption) is parked (PROJECT_BACKLOG.md Tier 3.5).
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-fleet-kba-mid-design.md`; plan
  `2026-06-07-fleet-kba-mid.md`; PROJECT_STATUS.md §2.3.

