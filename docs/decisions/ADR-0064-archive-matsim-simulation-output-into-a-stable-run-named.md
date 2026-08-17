# ADR-0064 — Archive MATSim `simulation_output/` into a stable run-named location (#156) (2026-07-15, PR #181 MERGED)

- **Context:** MATSim run results lived ONLY inside synpp hash-named cache dirs
  (`cache_.../matsim.simulation.run__<hash>.cache/simulation_output/`). Verified semantics (synpp 1.5.1,
  `pipeline.py:806`): a cache dir is wiped without warning whenever the SAME hash is re-executed, and hash-named
  dirs are opaque to humans — during the 2026-07-10 server cleanup the only copies of the 100% and 25% MATSim
  outputs were nearly deleted. The eqasim scenario-export `output_*` dirs do NOT contain the MATSim output
  (events/plans/ITERS). Flag default-on; OFF byte-identical, and the archive does not modify `simulation_output`
  itself (no scientific-output change).
- **Decision:** extend the terminal export stage `matsim.output` to mirror `simulation_output/` from
  `context.path("matsim.simulation.run")` into a stable `<output_path>/matsim_output/`. Two stdlib helpers in
  `matsim/output.py`: `mirror_directory_tree` (recreate tree; **hardlink** via `os.link`, **copy fallback** via
  `shutil.copy2` on `OSError`; rmtree an existing target first) and `archive_simulation_output` (rate logging +
  provenance + fail-clean). Gated on `run_matsim AND archive_matsim_output` (new flag, default ON).
- **Fallback transparency (CLAUDE.md):** the hardlink-vs-copy rate is always logged; a 100%-fallback
  (`hardlink_count == 0`, `file_count > 0`) emits a WARNING (source and target on different volumes -> the
  zero-extra-disk property was lost). Provenance `ARCHIVE_INFO.json` (mirrors `documentation/meta_output.py`)
  records `source_hash_dir`, UTC `created`, and file/hardlink/copy counts. If the run produced no output
  (`file_count == 0`, e.g. a stale/wiped cache) it raises a clear `RuntimeError` naming the source + #156 and
  leaves no half/empty archive — the exact loss scenario this feature guards against.
- **Rejected alternatives:** (a) a dedicated `matsim.archive` synpp stage — more wiring, must be added to every
  config, no benefit; (b) mirroring from inside `matsim.simulation.run` — that stage does not know `output_path`
  and would mix "run the simulation" with "export/archive". Chose extending `matsim.output` (A).
- **Testing note:** local `pytest` cannot collect the test module because the repo's namespace `matsim/` package
  (no `__init__.py`) is shadowed by a system `matsim-tools` install (documented, pre-existing; memory
  `reference-local-test-env-matsim-shadowing`); all six behaviours were verified via importlib load, formal
  pytest GREEN + e2e deferred to the server `eqasim` env.
- **Evidence:** PR #181 (Closes #156, MERGED `f83a81f`); subagent-driven TDD (3 tasks + per-task reviews + opus
  whole-branch review); memory `project-matsim-output-archive-156`; interim manual hardlink archives on felix
  under `eqasim-data/matsim_archive/` (2026-07-10).

