# ADR-0021 · 2026-06 · HSN/TSN scraper for engine attributes
- **Status:** active
- **Context:** Real engine attributes (kW, ccm, fuel) per vehicle model require an HSN/TSN lookup
  not shipped with KBA aggregates.
- **Decision:** Scrape hsn-tsn.de (`scripts/scrape_hsn_tsn.py`, 1 request/brand) into a kW/ccm/fuel
  lookup and map scraped brands onto the fleet (62-brand coverage).
- **Rationale:** Provides the per-model engine attributes the HBEFA wiring will need; mapping
  table covers all fleet brands (memory `hsn-tsn-scraper`).
- **Consequences:** Engine attributes present on vehicles; not yet consumed for emissions (Tier 3.5).
- **Evidence:** commits `1092221`, `4e231f9`; memory `hsn-tsn-scraper`; PROJECT_STATUS.md §2.3
  (KBA HSN/TSN scraper).

