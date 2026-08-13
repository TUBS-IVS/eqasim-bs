# ADR-0013 · 2026-06 · Driving licence as a categorical 3-margin IPF (MiD P17.1)
- **Status:** active
- **Context:** Licence was taken from KBA FE4.x via the IPF model; MiD P17.1 offers a directly
  conditioned categorical.
- **Decision:** Assign `license_type ∈ {ja,nein,keine_angabe}` to persons ≥18 from a 3-margin IPF
  on `Xl[kreis, sex, age_bin, license_category]` against MiD 2023 P17.1; `has_license = (type=="ja")`.
  The BF17/begleitetes-Fahren option is intentionally ignored.
- **Rationale:** MiD margins are integer-percent-rounded spanning 19–94%, so raking finds a
  least-squares compromise within ~10pp on the worst cell (CLAUDE.md "Driving licence (P17.1)").
- **Consequences:** The legacy KBA-FE4 `license` column is still produced but is no longer the source
  of truth; `keine_angabe` conservatively maps to False.
- **Evidence:** CLAUDE.md "Driving licence (P17.1)"; tests `test_license_ipf_three_margins_converges...`;
  PROJECT_STATUS.md §2.2 (MiD P17.1).

