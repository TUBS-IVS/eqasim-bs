# ADR-0024 · 2026-06-03 · Education gravity (real schools / Kita / university)
- **Status:** active
- **Context:** The generic OSM hard-radius education sampler ignores real facility capacity and
  distance decay, and uses coarse age bands.
- **Decision:** Assign all education levels by real-data distance-decay gravity: school-age pupils to
  real Niedersachsen schools (doubly-constrained capacity Furness), kindergarten to real Kita Plätze
  (same model), and university students to real Hochschulen (singly-constrained decay). Flag
  `education_gravity_enabled` (OFF = legacy OSM, byte-identical).
- **Rationale:** Doubly-constrained prevents a tiny nearby school swallowing pupils; the
  singly-constrained university choice lets the distance tail reach far universities whose huge
  enrollment is mostly non-resident; the 16–19 BBS/Oberstufe split (`education_bbs_share=0.681`) is
  NDS enrollment (CLAUDE.md "Education gravity model").
- **Consequences:** Real facilities (local-only data, not committed); per-(RS7,level) slopes
  calibrated to MiD T43 / Destatis MZ 2024; legacy bands change only on the ON path.
- **Evidence:** spec `docs/superpowers/specs/2026-06-03-education-gravity-design.md`; plans
  `2026-06-03-{education-gravity,kita-education,university-education,bbs-oberstufe-split,education-slope-calibration}.md`;
  `docs/features/education-gravity.md`; PROJECT_STATUS.md §2.4.

