# ADR-0071 — eqasim-java 2.2.0 matsim.output made e2e-green: in-commuter time imputation, gzip pin, #229 config (2026-07-23)

- **Status:** Accepted.
- **Context:** eqasim-java 2.2.0 (MATSim 2026w12, JDK 25) was integrated earlier (PR #208/#211), but
  `matsim.output` had never run to completion on the new stack. A 1-Kreis (03101) end-to-end run with
  freight ON surfaced four independent breakages between the pipeline and the 2.2.0 jar.
- **Decision:**
  1. **Freight CLI** adapted to the MATSim 2026 `longDistanceFreightGER` application contrib:
     `--LegMode`->`--legMode`, `--tripType`->`--geographicalTripType`, and an explicit
     `--subpopulation freight` (the tool now defaults to `longDistanceFreight`, but the whole
     downstream pipeline -- merge, replanning config, `analysis/freight_filter.py`, injection --
     keys on `freight`). This fix landed on `main` independently, so PR #239 does not carry it.
  2. **In-commuter routing:** ~16% of work (and ~2% of education) HTS donors have an untimed return
     leg -> NaN activity end_time -> MATSim "undefined activity end time". `impute_incommuter_times`
     fills the missing times from same-subpopulation fully-timed donors using a fixed-seed local
     `RandomState` -- deterministic, never touches the caller RNG, byte-identical when nothing is
     missing. Mirrors the established item-non-response imputation in `braunschweig/popsim/missing.py`
     (impute from comparable respondents) rather than a silent first/last fallback.
  3. **Compression:** pin `controler.compressionType=gzip`. MATSim 2026 defaults to `zst`, but the
     pipeline's existence asserts (`matsim.simulation.run`, `matsim.output` archive) and all
     downstream analysis consume the historical `.gz` names.
  4. **#229 config contract:** `matsim/output.py` reads `run_matsim` as a declared key (1-arg
     `context.config("run_matsim")`) in `execute`, matching the synpp per-stage contract (defaults
     belong in `configure`).
- **Consequences:** `matsim.output` is 8/8 green on 2.2.0 -- real QSim iteration 0 (SwissRailRaptor
  PT), 68 output files archived. The Guice/ASM "major version 69" line under JDK 25 was root-caused
  (via /systematic-debugging) as a NON-FATAL `printInjector` DEBUG diagnostic (caught), not a run
  blocker. No behavioural validation is implied: this is a wiring/green proof (convergence !=
  validation). Fixes on PR #239 (rebased clean onto `main`, MERGEABLE). The car/bike control-fit gap
  (urban-concentrated ~3.6-4.7pp on category shares) is tracked as issue #240 (MiD-informed 1 km
  disaggregation of the KREIS control). No scientific outputs change for existing runs: imputation is
  byte-identical when no times are missing; the gzip pin and the config read are behaviour-preserving.

> **Live status note.** This log is the retrospective *why*. For the current state of every feature
> (merged / flag-on / infra-only / open PR), always defer to [PROJECT_STATUS.md](../PROJECT_STATUS.md)
> and `git log`; where this log and those disagree, `CLAUDE.md` and git win.

