# ADR-0017 · 2026-06 · Income €, income-aware car count, consistent car availability, tenure
- **Status:** active
- **Context:** Several enrichment attributes needed to be made internally consistent and
  data-grounded: household income in €, number of cars, car availability, and housing tenure.
- **Decision:** Add flag-gated stages: `income_eur_from_distribution` (MiD H4/brackets + INKAR
  class-midpoint scaling), `cars_income_aware` (MiD H7), `consistent_car_availability`
  (MiD P19/P17.1/H7), and `synthesise_housing_tenure` (MiD income×Wohnen, for completeness).
- **Rationale:** Each is grounded in a committed MiD/INKAR reference table (CLAUDE.md MiD reference
  table inventory); all flag-gated so OFF is byte-identical.
- **Consequences:** Internally consistent socio-economic attribute set for the synthetic population.
- **Evidence:** CLAUDE.md "Reference data: MiD 2023 constraint tables"; PROJECT_STATUS.md §2.2.

