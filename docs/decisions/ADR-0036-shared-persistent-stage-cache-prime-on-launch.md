# ADR-0036 · 2026-06-22 · Shared persistent stage-cache (prime-on-launch)
- **Status:** active
- **Context:** Expensive sampling-rate-independent synpp stages (above all the ~3h freight Java
  routing) are recomputed on every fresh run because synpp caches per `working_directory`.
- **Decision:** Add `braunschweig.cache_share` + a `scripts/run_synpp.py` launcher that PRIMES a
  shared store before synpp runs and EXPORTS after a successful run, by copying synpp's cache
  artifacts (never recomputing synpp's hash — synpp re-validates the hash on load). Flags
  `cache_share_enabled` (true) / `cache_share_export` (true); `enabled: false` is a pure no-op.
- **Rationale:** We copy artifacts and let synpp decide validity, so a primed entry whose hash does
  not match is ignored and recomputed (never corruption, only a forgone speedup, logged as a miss)
  (`docs/features/cache-share.md`).
- **Consequences:** Freight routing runs once and is reused across runs/machines; auto-export uses
  `skip_existing=True` so the store is never overwritten.
- **Evidence:** spec `docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md`;
  spec `2026-06-23-auto-export-shared-cache-design.md`; `docs/features/cache-share.md`;
  `tests/test_cache_share.py`; PROJECT_STATUS.md §2.8.

