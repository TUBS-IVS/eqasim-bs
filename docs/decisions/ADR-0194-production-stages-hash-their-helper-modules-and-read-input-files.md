# ADR-0194 · 2026-11-12 · Production stages hash their helper modules, the stages they run as libraries, and the input files they read

- **Status:** accepted (owner, 2026-11-12: approval of the package C step plan of the repository
  audit follow-up, and three answers given in the implementing session: `matsim.runtime.java`
  gets a token; declared stages used as libraries are hashed everywhere; the two jar stages and
  `matsim.simulation.run` included). Supersedes ADR-0136 Decision 1 in one point: the closure
  walk no longer stops at a stage's declared synpp dependencies.
- **Numbering:** ADR-0194 is the next free id. Checked on 2026-11-12 across all local and remote
  branches: ADR-0193 is reserved for the first package C pull request (C1, C4).
- **Issue:** none; repository audit of 2026-11-11, package C, findings P1-F4, P6-F2, P7-F1,
  P5b-F3, A2-F1 (C2), P5a-F2, P9-F2, P10-F18, P10-F17 (C3) and N-F10 (the stale audit note),
  plus one gap found by the final review of this change (Decision 2).

## Context

- ADR-0136 gates every stage whose `validate()` hashes source on its whole import closure. The
  walk started at the stage module's own static imports. A module the stage loads through
  `importlib` and lists in `_DEFERRED_HELPER_MODULE_NAMES` (the donor-source registry of
  `braunschweig.popsim.trips_stage`) was hashed, but what it imports was never walked (P1-F4).
- The audit treated a declared synpp dependency that a stage imports as covered by synpp's DAG
  and stopped the walk there. Under content-addressed validity (ADR-0149) that is false for
  library code: an edit to the parent module (`matsim.runtime.java.run`, `data.osm.osmosis.run`,
  `braunschweig.data.bbsr.regiostar.ars_to_ags8`) re-executes the parent, an unchanged parent
  output spares the child (the early cutoff in `run` of `braunschweig/synpp_fork/pipeline.py`),
  and the child keeps a cache built with the old code it runs itself.
- Stages without a source token are not gated at all. Production (`configs/base_bs.yml` +
  `configs/overlays/test_100pct.yml`, aliases resolved) reached 26 stages with first-party
  helpers and no token (`matsim.simulation.run` among them), and 7 more whose only first-party
  helpers were declared stages they run as libraries.
- Four input files were read by stage code without being in `validate()`: the two SrV-2023
  location-type tables of the chainsolver stage, the LoD2 heights parquet of the buildings
  stage, the VG250 archive of the RegioStaR stage. The committed VRB matrix CSV was in the
  token as raw bytes, which differ between a Windows (`core.autocrlf`) and a Linux checkout.
- `tests/test_synpp_helper_hash_invariant.py` carried a deliberate exception for
  `matsim.runtime.java` -> `matsim.runtime.process_watchdog` (#330), because hashing the watchdog
  would re-run `matsim.runtime.java` and its dependents (ADR-0095).

## Decision

1. The closure walk (`import_closure` in `scripts/audit_synpp_helper_hash.py`) also starts at
   every module the stage's helper tuples name. The rule is stated in
   `docs/codebase/notes/synpp-helper-hash-audit.md`.
2. A declared stage module that a stage imports is a required helper like any other, and the
   walk goes through it: the DAG edge covers the parent's output, never the library code the
   child runs. Both resolvers apply it (`scripts/audit_synpp_helper_hash.py`,
   `tests/test_synpp_helper_hash_invariant.py`).
3. Every production stage with first-party helpers hashes its whole import closure, following
   `validate` of `braunschweig/synthesis/incommuters.py`; a stage whose `validate()` already
   returned a file size or a digest keeps it next to the module digests (`_COMPOSITE_STAGES`).
   This includes `matsim.runtime.eqasim` and `matsim.runtime.pt2matsim` (they run `git.run`,
   `maven.run`, `java.run`) and `matsim.simulation.run` (it runs `eqasim.run` -> `java.run`).
   Over-hashing is accepted: `trips_stage` hashes everything its donor adapters import, and
   `braunschweig.data.cordon_pt_gates` hashes the closure of the `incommuters` stage module
   because `pt_reachability` reads `RAIL_LIKE_MODES` from it. A closure boundary is used only
   where its condition holds: `cut_schedule` never runs `matsim.runtime.git` or `maven`.
4. `matsim.runtime.java` hashes `process_watchdog`; the #330 exception is removed. The watchdog
   thresholds stay volatile config keys (ADR-0095), so tuning them re-runs nothing.
5. `braunschweig.parking.runtime_checks` declares `_SYNPP_TOKEN_EXEMPTION`: `matsim.simulation.run`
   uses it only to decide whether to run two checks that raise or pass. An edit to a check
   therefore does not re-run MATSim; an edit to a Java runner module does.
6. Input files enter `validate()` as content digests, from two helpers in
   `braunschweig/synpp_tokens.py`: `file_sha256` (raw bytes, for downloads and binaries) and
   `text_file_sha256` (CRLF normalised to LF, for committed text tables). The chainsolver adds the
   SrV table digests only while `secondary_srv_location_types` is on, so its OFF token is the
   plain source token. The buildings stage returns a fixed marker when the optional LoD2 file is
   absent and warns with the footprint count when no footprint gets a height.

## Rejected alternatives

- **Fix the declared-library gap only where it costs no extra re-execution, and issue the rest.**
  Offered; the owner chose the complete fix.
- **Leave `matsim.runtime.eqasim`, `matsim.runtime.pt2matsim` and `matsim.simulation.run` without
  a token.** Offered because their tokens re-run the Java-dependent stages after an edit to a
  runner module; the owner chose consistency. The offer also said that the jars are not
  byte-reproducible, which is outdated: the eqasim and freight-tool builds are byte-reproducible
  since #474 (ADR-0149 decision 8, `docs/runs/reproducible-jar-builds-local-2026-10-22.yml`,
  `docs/runs/reproducible-jar-builds-felix-2026-10-22.yml`), so the cost is lower than stated.
- **Exempt `process_watchdog`.** Three stages (`supply_gtfs`, `simulation.prepare`,
  `student_incommuters`) hashed it already; an exemption would change their tokens, and the
  watchdog decides whether a Java call is killed, so it is not plainly result-neutral.
- **Keep the #330 exception.** The java stage would stay the one production stage whose helper is
  outside every check.
- **A boundary at `incommuters` for `cordon_pt_gates`.** The stage reads a constant of that module,
  so the boundary's claim ("never runs it") would be false.
- **Tokens for the stages that only fixture configs reach** (stages without a token whose module
  is not a node of the production DAG snapshot `docs/registry/dag/production.json` after alias
  resolution). No production run reads their cache; the own-package invariant still lists them.
- **File sizes instead of digests** for the four files. A corrected table of equal size would keep
  a stale cache.
- **Raw bytes for committed text.** One commit would give two tokens, depending on the platform.

## Consequences

- Every stage whose token is new or changed re-executes once, and so does every stage whose token
  hashes a module this change edited. Recompute the list with the `covered` field of
  `python scripts/audit_synpp_helper_hash.py . --json <out.json>` against
  `git diff --name-only` of this change; the pull request of this change lists the result.
  `matsim.runtime.eqasim` rebuilds a byte-identical jar (#474), so the jar itself spares its
  children; MATSim and the other stages that run `eqasim.run` re-execute because their own
  tokens changed. The pt2matsim build is not made reproducible: ASSUMPTION (not measured) that
  its jar differs after a rebuild, so every stage below it re-executes too. Elsewhere children
  are spared only where the re-executed parent reproduces its output byte for byte; that is not
  measured for this change.
- No stage computes anything differently: outputs do not change, except that the buildings stage
  now prints a warning when no footprint gets an LoD2 height.
- From now on an edit to a module a stage runs re-executes that stage, a declared stage it runs
  as a library included: an edit to the popsim assembly code re-runs `trips_stage`; an edit to
  `matsim/runtime/{git,maven,java,process_watchdog}.py` re-runs every stage whose token hashes it:
  the jar stages, MATSim and every other stage that runs Java.
- Open, not fixed here: other stage modules read VG250 directly without it in `validate()`, and
  the composite stages `external_workplaces` and `external_secondary_points` key on the VG250
  file SIZE, so a replaced archive of equal size re-runs `regiostar` but not them (the other
  composite stages likewise keep the size-only file parts they had: OSM, GTFS, the NDS school
  tables); no scan finds
  a file read that `validate()` misses; the audit's scan roots exclude `documentation.meta_output`
  (left to package M of the audit follow-up).
