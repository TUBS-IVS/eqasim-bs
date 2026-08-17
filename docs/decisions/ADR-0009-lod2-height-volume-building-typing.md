# ADR-0009 · 2026-06-17 · LoD2 height/volume building typing
- **Status:** active
- **Context:** Building type and dwelling capacity were inferred from footprint area alone, which
  cannot distinguish a tall apartment block from a large flat building.
- **Decision:** Join LoD2 3D building heights by ALKIS `OI` (non-destructive, coverage logged) and
  type/size buildings by `building_volume(area, height)` end-to-end (volume-rank MFH typing,
  `MFH_MIN_FLOORS=4`, volume-weighted slots).
- **Rationale:** `MFH_MIN_FLOORS=4` was tuned on a Salzgitter real-population sweep; the consumer
  side is fully wired and verified 2026-06-27 (PROJECT_BACKLOG.md §2.2).
- **Consequences:** Better dwelling-capacity realism feeding ADR-0008.
- **Evidence:** spec `docs/superpowers/specs/2026-06-17-lod2-height-volume-capacity-design.md`;
  plan `2026-06-17-lod2-height-volume-capacity.md`; PROJECT_STATUS.md §2.1
  (verified 2026-06-27); `test_preprocess_alkis_oi.py`.

