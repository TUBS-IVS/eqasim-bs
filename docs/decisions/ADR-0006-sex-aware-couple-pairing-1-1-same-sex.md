# ADR-0006 · 2026-06 · Sex-aware couple pairing (~1.1% same-sex)
- **Status:** active
- **Context:** Sex-blind age-adjacent couple pairing yields ~48% same-sex couples (every pair is
  sex-random), which is grossly unrealistic.
- **Decision:** Pair couples opposite-sex by default with a small calibrated same-sex share
  (`DEFAULT_SAME_SEX_COUPLE_SHARE=0.011`) via an opposite-first allocation `max(intended, forced)`
  that never drops anyone. Flag `chunking.sex_aware_couples` (OFF = legacy sex-blind, byte-identical).
- **Rationale:** 1.1% is Statistisches Bundesamt Mikrozensus 2025 (204,000 same-sex couples,
  ~50/50 male/female, vs ~18.9M couples) — a committed reference
  (`docs/features/household-synthesis.md`).
- **Consequences:** Realised share converges toward 1.1% as sampling rate rises (~2.9% at 25%,
  the residual being genuine local sex imbalance in small Gemeinden).
- **Evidence:** `docs/features/household-synthesis.md`; PROJECT_STATUS.md §2.1 (Destatis MZ 2025).

