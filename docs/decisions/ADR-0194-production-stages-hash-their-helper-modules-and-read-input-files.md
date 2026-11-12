# ADR-0194 · 2026-11-12 · Production stages hash their helper modules and the input files they read

- **Status:** accepted (owner, 2026-11-12: approval of the package C step plan of the repository
  audit follow-up, and the answer on `matsim.runtime.java` given in the implementing session).
- **Numbering:** ADR-0194 is the next free id. Checked on 2026-11-12 across all local and remote
  branches: ADR-0193 is reserved for the first package C pull request (C1, C4).
- **Issue:** none; repository audit of 2026-11-11, package C, findings P1-F4, P6-F2, P7-F1,
  P5b-F3, A2-F1 (C2), P5a-F2, P9-F2, P10-F18, P10-F17 (C3) and N-F10 (the stale audit note).

## Context

- ADR-0136 gates every stage whose `validate()` hashes source on its whole import closure. The
  walk started at the stage module's own static imports. A module the stage loads through
  `importlib` and lists in `_DEFERRED_HELPER_MODULE_NAMES` (the donor-source registry of
  `braunschweig.popsim.trips_stage`) was hashed, but what it imports was never walked, so the
  gate could not see that code (P1-F4).
- Stages without a source token are not gated at all. 26 stages reached by the production
  config (`configs/base_bs.yml` + `configs/overlays/test_100pct.yml`) had first-party helpers
  and no token, so an edit to one of those helpers served a stale cache.
- Four input files were read by stage code without being in `validate()`: the two SrV-2023
  location-type tables of the chainsolver stage, the LoD2 heights parquet of the buildings
  stage, the VG250 archive of the RegioStaR stage. The committed VRB matrix CSV was in the
  token as raw bytes, which differ between a Windows (`core.autocrlf`) and a Linux checkout.
- `tests/test_synpp_helper_hash_invariant.py` carried a deliberate, permanent exception for
  `matsim.runtime.java` -> `matsim.runtime.process_watchdog` (#330): hashing the watchdog would
  re-run `matsim.runtime.java` and its 22 dependents (about 1.5 h at 100 %, ADR-0095) on every
  watchdog edit. That cost was measured before the content-addressed cache.

## Decision

1. The closure walk (`import_closure` in `scripts/audit_synpp_helper_hash.py`) also starts at
   every module the stage's helper tuples name, a declared stage the stage also lists in a tuple
   included: listing it states that the stage runs its code itself (`cut_schedule` calls
   `matsim.runtime.eqasim.run`). The rule is stated in `docs/codebase/notes/synpp-helper-hash-audit.md`.
2. Every production-reachable stage with first-party helpers hashes its whole import closure,
   following `validate` of `braunschweig/synthesis/incommuters.py`; a stage whose `validate()`
   already returned a file size keeps it next to the module digests (`_COMPOSITE_STAGES`).
   Over-hashing is accepted: `trips_stage` hashes everything its donor adapters import, and
   `braunschweig.data.cordon_pt_gates` hashes the whole closure of the `incommuters` stage module
   because `pt_reachability` reads `RAIL_LIKE_MODES` from it. A closure boundary is used only
   where its condition holds: `cut_schedule` never runs `matsim.runtime.git` or `maven`.
3. `matsim.runtime.java` gets a token that hashes `process_watchdog`; the #330 exception is
   removed. Its `execute()` writes nothing, so a re-executed java stage reproduces its output.
   The watchdog thresholds stay volatile config keys (ADR-0095), so tuning them re-runs nothing.
4. `braunschweig.parking.runtime_checks` declares `_SYNPP_TOKEN_EXEMPTION`: `matsim.simulation.run`
   uses it only to decide whether to run two checks that raise or pass, and stays without a token.
5. Input files enter `validate()` as content digests, from two helpers in
   `braunschweig/synpp_tokens.py`: `file_sha256` (raw bytes, for downloads and binaries) and
   `text_file_sha256` (CRLF normalised to LF, for committed text tables). The chainsolver adds the
   SrV table digests only while `secondary_srv_location_types` is on, so its OFF token is the
   plain source token. The buildings stage returns a fixed marker when the optional LoD2 file is
   absent and now warns with the footprint count when every `height_m` is NaN.

## Rejected alternatives

- **Exempt `process_watchdog`.** Three stages (`supply_gtfs`, `simulation.prepare`,
  `student_incommuters`) hash it today; an exemption would change their tokens, and the watchdog
  decides whether a Java call is killed, so it is not plainly result-neutral.
- **Keep the #330 exception and leave `matsim.runtime.java` without a token.** The java stage
  would stay the one production stage whose helper is outside every check; the re-run cost that
  justified the exception is now mostly spared because the stage's output never changes.
- **A token on `matsim.simulation.run`.** An edit to a check would force a full MATSim run.
- **A boundary at `incommuters` for `cordon_pt_gates`.** The stage reads a constant of that module,
  so the boundary's claim ("never runs it") would be false.
- **Tokens for the 16 stages that only fixture configs reach.** No production run reads their
  cache; the own-package invariant still covers their sibling modules.
- **File sizes instead of digests.** A corrected table of equal size would keep a stale cache.
- **Raw bytes for committed text.** One commit would give two tokens, depending on the platform.

## Consequences

- Each stage with a new or changed token re-executes once, and so does every stage whose token
  hashes a module this change edited (41 stages; recompute with the `covered` field of
  `python scripts/audit_synpp_helper_hash.py . --json <out.json>` against the changed modules).
  Besides the 25 stages that gained a token and the four C3 stages, these are the stages that hash
  `regiostar` (`analysis_suite`, `simwrapper_export`, `plan_structure_vs_srv`,
  `work_participation_by_kreis`, `data.hts.mid_donor`, `trips_stage`), `external_workplaces`
  (`incommuters`, `student_incommuters`), `matsim.runtime.java` (`supply_gtfs`,
  `simulation.prepare`, `student_incommuters`), `cut_schedule` (`simulation.prepare`) and
  `trips_stage` (`home_office_donors_stage`). Children of a re-executed stage are spared only
  where it reproduces its output byte for byte; that is not measured for this change. No stage
  computes anything differently: outputs do not change, except that a missing LoD2 file now
  prints a warning.
- An edit to the popsim assembly code now re-runs `trips_stage`; an edit to the watchdog re-runs
  `matsim.runtime.java` once.
- Open, not fixed here: other stage modules read VG250 directly without it in `validate()`,
  no scan finds a file read that `validate()` misses, and the audit's scan roots exclude
  `documentation.meta_output` (left to package M of the audit follow-up).
