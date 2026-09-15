# Result-preserving runtime optimization

This change removes repeated work in four Python kernels. The scientific
configuration and random draw order remain unchanged. Decision:
[ADR-0122](../../decisions/ADR-0122-result-preserving-performance-kernels.md).
Executed measurements and their limitations:
[run manifest](../../runs/performance-equivalence-2026-09-15.yml).

## Switches and scope

All switches live in `configs/base_bs.yml` and default to true.

| Switch | Optimized operation | OFF behavior |
|---|---|---|
| `braunschweig.performance.fleet_home_lookup` | Index the first joined home row per household | Original household-by-household frame filter |
| `braunschweig.performance.home_coordinates` | Project signals to household cells, build/match slots with arrays, and batch coordinate transforms | Original full cell loader, scalar slot/matcher kernels and scalar CRS round trip |
| `braunschweig.performance.plan_validation` | Check trip arrays grouped at person boundaries | Original per-person validator |
| `braunschweig.performance.donor_matching` | Prepare invariant donor features and candidates | Original feature extraction per match |
| `cache_share_metadata` | Transfer coherent native synpp metadata | Original artifact-only cache copy |

To disable a switch, copy the desired scale overlay outside tracked configuration
and add that key with `false` to its `config` mapping. Run the unchanged base
with this temporary overlay. Each switch can be disabled independently.

The typed-home optimization reads the grid id plus the 38 signal columns used by
home matching, retains only source-ordered household cells, and builds per-cell
histograms for those rows. Native numeric signal columns take a dtype-only
validation fast path; unusual object or nullable size-bin columns replay the old
value conversion before filtering so malformed unused rows still fail. Indexed
slot construction and matching preserve the existing pandas sort order, Hamilton
rounding and over-capacity choice. Centroid-only and legacy placement retain their
existing behavior. Preparation belongs to the optimized invocation and is included
in measurements. No global mutable donor cache is introduced.

The plan-validation switch reaches both the main MiD trip stage and the home-office
donor trip builder. The common source interface accepts it for ENTD, whose separate
trip builder does not use `PlanValidator`. All existing validation and repair passes
remain in place; only each validation pass's grouped checks use arrays.

Coordinate batches finish before the next cell's matching or any random fallback.
On a batch error, deterministic scalar replay reproduces the original first input
error. If scalar replay succeeds, the batch error is surfaced instead of hidden.

## Reproduce exactness and timings

Use the same Python environment for both checkouts. The reference checkout must
be at the desired original revision; the report records both commit IDs and
whether either checkout contains uncommitted changes.

```bash
python scripts/benchmark_performance_equivalence.py \
  --reference-root /path/to/original-checkout \
  --size 1000 --repeat 3 --output /path/to/measurement.json
```

The CLI runs original/OFF/ON in separate processes and checks exact values,
dtypes, row/column order, geometry WKB, reports and donor RNG state. It fails
before producing a success report if any comparison differs. Timings cover
public functions after imports and fixture construction; index/pool preparation
inside those functions is included. Synthetic workloads are bounded: 300 home
households and 200 donor targets at most, with configurable fleet/validator sizes.
`--case` selects one kernel. `--input-bundle` accepts an explicitly chosen,
trusted pickle mapping case names to the fixture dictionaries created by
`make_fixture`; it is intended for local, uncommitted cached-input probes.
Donor comparisons default to the production fine child age bands;
`--donor-age-bands coarse` checks the older band definition explicitly. The same
edges are passed to the original matcher, OFF matcher and prepared ON matcher.

The bounded CLI evidence is complemented by a production-size typed-home replay:
558,279 households, 310,512 buildings and 38,483 used cells. The projected path
completed in 547.419 s; adding array slot construction and matching reduced that
to 410.853 s (24.95%, 1.332x). Values, dtypes, row/column order, geometry WKB,
CRS, `TypedHomeReport`, random-call counts and final RNG state were exact. This
is full-input evidence for the typed-home public function, not a full synpp or
MATSim replay and not behavioural validation.

## Shared cache correctness

Artifact filenames encode configuration identity; they do not by themselves
prove that a result is current. The shared store now carries a native
`<entry>.metadata.json` envelope from the source run, with OS, architecture,
Python and installed package versions. Only entries with a new or changed native
`updated` timestamp during a successful tracked launcher run receive creation
provenance. Unchanged native hits without provenance stay unknown. Export carries
existing envelopes unchanged, even if its own environment differs.

After copying, prime merges only records with exactly matching runtime provenance
and coherent dependencies into the target `pipeline.json`. synpp still checks
module source, configuration, input validation tokens and dependencies.

For copied entries and affected descendants, dependency timestamps must match
exactly. This prevents a child from one run being paired with an older parent
from another run. Missing metadata or mismatched provenance is logged and
recomputed. Existing payloads are never overwritten during prime; existing
metadata may be invalidated if a newly imported parent makes it incoherent.
Unrelated target metadata is retained.

Each result file and cache directory is copied into a temporary entry first.
The complete cache directory is published before the result file used for entry
discovery. A failed copy removes only temporary artifacts; a failed re-export
keeps the previously published result, cache directory and metadata intact. This
prevents automatic `skip_existing` exports from permanently accepting a result
file paired with a partial cache directory.

Legacy cache creation environments cannot be reconstructed. To seed a usable
store, execute the desired stages through the updated launcher in a fresh working
directory, then export that tracked, completed cache using
`scripts/cache_share.py export`. Merely exporting old artifacts adds no
provenance. Automatic export
uses `skip_existing=True`, so it intentionally does not retrofit metadata onto
unknown existing bytes. Export and prime require exclusive destination access.
This guard prevents newly shared Windows results being reused on Linux when
native integer dtypes differ. It does not redesign native synpp's assumptions
about valid local caches or configured external tools.

The 1% and 25% overlays retain their shared PopulationSim working directory and
uniform importance weights. The 100% overlay keeps its distinct working directory
and `optimized_2026_06_30` weights. These are different scientific computations.
