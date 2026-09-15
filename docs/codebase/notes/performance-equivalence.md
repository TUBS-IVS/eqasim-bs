# Result-preserving runtime optimization

This change removes repeated work in four Python kernels. The scientific
configuration and random draw order remain unchanged. Decision:
[ADR-0121](../../decisions/ADR-0121-result-preserving-performance-kernels.md).
Executed measurements and their limitations:
[run manifest](../../runs/performance-equivalence-2026-09-15.yml).

## Switches and scope

All switches live in `configs/base_bs.yml` and default to true.

| Switch | Optimized operation | OFF behavior |
|---|---|---|
| `braunschweig.performance.fleet_home_lookup` | Index the first joined home row per household | Original household-by-household frame filter |
| `braunschweig.performance.home_coordinates` | Transform selected footprint/cell pairs in batches | Original scalar CRS round trip |
| `braunschweig.performance.plan_validation` | Check trip arrays grouped at person boundaries | Original per-person validator |
| `braunschweig.performance.donor_matching` | Prepare invariant donor features and candidates | Original feature extraction per match |
| `cache_share_metadata` | Transfer coherent native synpp metadata | Original artifact-only cache copy |

To disable a switch, copy the desired scale overlay outside tracked configuration
and add that key with `false` to its `config` mapping. Run the unchanged base
with this temporary overlay. Each switch can be disabled independently.

Home batching applies to the typed footprint path; centroid-only and legacy
placement retain their existing behavior. The preparation cost belongs to the
optimized invocation and must be included in a benchmark. No global mutable
donor cache is introduced.

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

This is execution-equivalence evidence for the tested inputs. It does not
constitute a 100% synthesis/MATSim replay or behavioural validation.

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
