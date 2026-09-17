# ADR-0122 · 2026-09-15 · Optimize repeated work while preserving simulation inputs

## Status

Implemented on the performance worktree; maintainer acceptance pending.
Originally drafted as ADR-0121 after reserving 0119/0120 for sibling branches.
Renumbered to ADR-0122 when integrating main at 31fc30fd: PR #399 had landed
the passenger-availability decision as ADR-0121 before this branch was pushed.

## Context

The workflow repeats household home-frame filtering, single-point coordinate
transformations, per-person validation frame construction and donor feature
extraction. The requested optimization must preserve the previous results,
including seeded choices and diagnostic reports. The reference implementation
is commit `1502e88ef529001167f4a8848892862c39bda9cb`.

Shared cache priming also copied native artifacts without their `pipeline.json`
records. Real synpp tests showed that a fresh target executed the stage again.
Native synpp compares dependency timestamps using a greater-than check; merging
unrelated run snapshots therefore requires an additional exact-coherence check.

## Decision

1. Build a stable first-home-row index after the original fleet joins.
2. For typed homes, load only the prepared-cell signal columns and source-ordered
   household cells consumed by matching. Build slots and match households through
   indexed arrays while replaying the legacy pandas sort order, Hamilton rounding
   and over-capacity selection. Batch only selected deterministic footprint/cell
   coordinate pairs, retaining the exact CRS round trip and the original positions
   of random fallback draws.
3. Validate sorted trip arrays at person boundaries while retaining issue ordering,
   all validation/repair passes and the scalar reference path.
4. Prepare invariant donor features and candidate data within an explicit pool
   lifetime; keep candidate order, weighting and seeded choice calls unchanged.
5. Export native synpp metadata and tracked creation-environment provenance beside
   each shared artifact. Prime only matching runtime environments and coherent
   dependency snapshots; absent or mismatched provenance forces recomputation.
   Preserve existing target payloads and unrelated records. Never infer metadata
   for an old store or attach another run's metadata to a skipped payload. Stage
   result and cache artifacts together, publishing the result file only after the
   cache directory is complete; preserve an existing entry on copy failure.

Each kernel has an independent default-ON switch and executable OFF path.
`cache_share_metadata: false` retains artifact-only transfer.

## Rationale

Removing repeated deterministic work can accelerate execution without changing
the scientific problem or partitioning random streams. Increasing worker counts,
changing shards, adopting a different floating-point solver or harmonizing the
100% importance profile would require separate reproducibility evidence.

## Consequences

Index and prepared-pool memory must remain bounded by actual inputs/requests.
New switches and source hashes cause an expected one-time cache invalidation.
Metadata-free stores need a new tracked execution before their provenance is known;
automatic export deliberately leaves existing store entries untouched.
OS, Python and installed-package version mismatches safely miss. Native entries
receive creation provenance only when their update timestamp changes during a
successful launcher invocation; old cache hits are never relabelled at export.
Cross-run metadata mismatches safely miss even when the payloads might happen
to be equal. Concurrent writes to the same cache destination remain unsupported.

Worker counts, shard sizes, seeds, weights and model defaults remain unchanged.
The 1%/25% shared uniform profile and the distinct optimized 100% profile remain
separate. A measured kernel speedup is not a full-run speedup.

The production-size typed-home public-function replay used 558,279 households,
310,512 buildings and 38,483 cells. Signal projection plus coordinate batching
completed in 547.419 s; array slot construction and matching reduced this to
410.853 s (24.95%, 1.332x). Exact scalar values, dtypes, ordering, geometry WKB,
report and random state matched. The result does not claim a full synpp or MATSim
runtime improvement.

README impact: existing installation, data acquisition and run commands remain
applicable. Benchmark usage and cache migration are documented in the linked note.

## Evidence

- [Implementation and reproduction](../codebase/notes/performance-equivalence.md)
- [Feature registry](../registry/features/performance_equivalence.yml)
- [Run manifest](../runs/performance-equivalence-2026-09-15.yml)
- Real synpp cache tests: `tests/test_cache_share_synpp.py`.
- The four `tests/test_performance_*.py` kernel modules and
  `tests/test_home_matching_arrays.py` pin OFF/ON equivalence;
  `scripts/benchmark_performance_equivalence.py` compares against original code.
