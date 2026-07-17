# Upgrade prep: synpp 1.5.1 -> 1.6.3 (#205) and eqasim-java 1.5.0 -> 2.2.x (#204)

> Investigation-only, 2026-07-17. Neither upgrade is executed here: both are multi-step
> infra changes (env recreation / Maven build + server smoke) that must be run
> deliberately with a green light. This doc records the decisive findings so the actual
> upgrade is low-surprise.

## #205 — synpp 1.5.1 -> 1.6.3

### Decisive finding: the upgrade is cache-safe by default

The three relevant PRs (#86 volatile options, #89 propagation fix, #94 resolution
speedup) all touch `src/synpp/pipeline.py`, i.e. the stage-hashing code — so cache
compatibility was the real risk. It is resolved:

- **#86** changes `hash_name(name, config)` to `hash_name(name, config, volatile)`:
  ```python
  hash.update(json.dumps({k: v for k, v in config.items() if k not in volatile}, sort_keys=True)...)
  ```
  With `volatile = set()` (the default: every `context.config(...)` call still passes
  `volatile=False`), the filtered dict equals the full dict, so the hash is
  **byte-identical** to 1.5.1's `json.dumps(config, sort_keys=True)`.
- **#94** adds a new `calculate_identification_hash` for the resolution speedup, but the
  stage **cache key** (`stage["hash"]`) still goes through `hash_name` unchanged.

**Conclusion:** upgrading does NOT devalidate existing `cache_share` entries as long as
we do not retroactively flag existing options `volatile=True`. The volatile feature is
opt-in and only re-hashes stages whose options we deliberately mark.

### Payoff (separate, opt-in)

Mark result-neutral operational options volatile so they stop devalidating the cache:
`num_workers` / `processes` are the prime candidates. Each option marked volatile
re-hashes the stages that read it once (a one-time recompute), so do it at a natural
recompute point, not mid-run (cf. the no-divergent-branch-against-shared-cache rule).

### Execution checklist (needs a green light)

- [ ] Bump `synpp==1.6.3` in `environment.yml`; update the `eqasim` env locally + on felix.
- [ ] Verify the new `calculate_identification_hash` / resolution logic does not change
      which stages are considered stale on our pipeline (run a resolve dry-run against a
      primed cache and confirm 0 unexpected recomputes).
- [ ] Full pytest suite green under 1.6.3 (local + felix).
- [ ] 1-Kreis smoke: outputs byte-identical vs 1.5.1.
- [ ] Then (optional, separate PR): mark `num_workers`/`processes` volatile.

## #204 — eqasim-java-bs 1.5.0 -> upstream eqasim-java 2.2.x

### Delta inventory

- Our fork (`../eqasim-java-bs`, wired via `eqasim_source_path`) is at
  **version 1.5.0**, branch `bavaria-main`, base commit `3f7da9b`
  (`matsim/runtime/eqasim.py`: `DEFAULT_EQASIM_VERSION = "1.5.0"`,
  `DEFAULT_EQASIM_COMMIT = "3f7da9b"`). The fork is present locally at the sibling path.
- Upstream is **2.2.0** (released 2026-06-03) plus later commits. This is a **major**
  jump (1.5 -> 2.2), not a small bump: MATSim was updated (2026w12), `javalin` 7.x,
  jackson 2.22, and API changes (`VehicleTourConstraint` replacing
  `EqasimVehicleTourConstraint`, `PersonInitializedEvent` in activity analysis).
- Our fork carries BS-specific patches on top of 1.5.0: freight extraction/injection
  wrapper, DMC isolation (`KeepLastSelected` selector), freight-agent analysis exclusion
  (visible in the fork's recent log: `c3d181d`, `03a9194`, `ad5c231`), plus urban
  parking and simwrapper wiring referenced in the Python side.

### High-value targets in 2.2.x (why the jump is worth it)

- **Standalone mode choice** (incl. DRT/feeder-DRT) — enables cheap ASC-calibration
  iterations without full runs (backlog #3, our largest open scientific item).
- **Travel-time comparison** (routed vs realized from events) — the bias check that
  should precede any mode-choice calibration.
- **Automated simulation restarts** + termination-criterion restart fixes — we use
  `eqasim:termination`; valuable for long felix runs.
- **VDF overhaul** (tested static/dynamic engine) — potential large speedup for 100% ZGB.

### Execution plan (needs a green light; heavy, cannot be done autonomously here)

1. Inventory the fork's commit delta vs its 1.5.0 base (`git log 3f7da9b..HEAD` in the
   fork) and categorize each BS patch (freight, DMC, parking, simwrapper).
2. Create a rebase/merge branch of the fork onto upstream 2.2.x; resolve conflicts,
   especially around the renamed constraint API and activity-analysis events.
3. Update `DEFAULT_EQASIM_VERSION`/`COMMIT`/`BRANCH` in `matsim/runtime/eqasim.py`;
   port the `--activity-types` config wiring (eqasim-france #531) needed for custom
   purposes (couples to #201 escort).
4. Maven rebuild; run the fork's Java tests.
5. One 1% e2e smoke (Python pipeline against the new jar) and sanity-check outputs vs a
   pre-update reference run (harness-validation rule) before any production run.
6. Record the eqasim version bump in RUNS provenance (reproducibility).

Follow-up issues to file once the jar is updated: actually *use* standalone mode choice
and travel-time comparison in the mode-choice calibration wave.
