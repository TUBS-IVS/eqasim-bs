# ADR-0018 · 2026-06-07 · Reactivated person attributes (couple/studies/single-parent-child)
- **Status:** active
- **Context:** Some eqasim person attributes were dormant in the BS path and needed real-data
  grounding to be reactivated.
- **Decision:** Reactivate the attributes (flag `reactivate_person_attributes`) grounded on
  Destatis education data (e.g. student share).
- **Rationale:** spec "Tier-A attribute reactivation" (2026-06-07); grounded on Destatis education.
- **Consequences:** Restores attributes used downstream; flag-gated.
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-tier-a-attribute-reactivation-design.md`;
  plan `2026-06-07-tier-a-attribute-reactivation.md`; PROJECT_STATUS.md §2.2 (Destatis education).

---

## Vehicle fleet

