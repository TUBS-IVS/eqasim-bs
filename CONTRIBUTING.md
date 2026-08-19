# Contributing to eqasim-bs

This project is **research software**: correctness, reproducibility, traceability, and
clarity outrank speed. Read [CLAUDE.md](CLAUDE.md) for the full binding rules — this file
is the practical workflow contract for humans and AI agents alike.

## The one canonical feature workflow

Every non-trivial feature/fix follows the **same** loop. (For Claude sessions this maps
onto the `superpowers` skill chain, named in brackets.)

1. **Start** — check which skills/conventions apply before acting `[using-superpowers]`.
2. **Brainstorm** — agree the design first; write a spec to
   `docs/superpowers/specs/YYYY-MM-DD-*.md` `[brainstorming]`.
3. **Plan** — write a step-by-step execution plan to `docs/superpowers/plans/YYYY-MM-DD-*.md`
   `[writing-plans]`.
4. **Isolate** — work on a branch/worktree off `main`; parallel agents get **separate
   worktrees**, never one working dir `[using-git-worktrees]`.
5. **Implement TDD** — red → green → refactor. New behaviour is **flag-gated, default-ON**
   with an explicit byte-identical **OFF-path test** `[test-driven-development]`.
6. **Verify** — real evidence: run the suite on the **server** when matsim-shadowing breaks
   local imports; a **1% real smoke** beats mocked tests `[verification-before-completion]`.
7. **Review** — request a review before merging `[requesting-code-review]`.
8. **Land** — open the PR **always via `git pr`** (base = the fork `TUBS-IVS/eqasim-bs`,
   never the `eqasim-org/eqasim-bavaria` upstream); after merge, delete the branch + prune
   the worktree `[finishing-a-development-branch]`.
9. **Record** — update the **model registries** for what changed (new/changed stage →
   `docs/registry/stages/`, feature → `docs/registry/features/`, dataset →
   `docs/registry/data/` + `scripts/verify_braunschweig_inputs.py` + README data setup),
   add an **ADR** (`docs/decisions/ADR-NNNN-*.md`) for any substantive decision, add a
   **run manifest** (`docs/runs/<run_id>.yml`) if a significant run happened, then
   `python -m braunschweig.documentation build && python -m braunschweig.documentation check`
   and commit the regenerated `docs/generated/*`. Update `SESSION_LOG.md` (local), sync the
   GitHub Project board, and close the issue. See
   [docs/DOCUMENTATION_GOVERNANCE.md](docs/DOCUMENTATION_GOVERNANCE.md) for the full
   ownership model.

`/close` enforces step 9 at session end. (`PROJECT_STATUS.md`, `PROJECT_BACKLOG.md` and
the `RUNS.md` ledger are retired pointer stubs — never write status/backlog/run rows there.)

## Branches & PRs

- Branch naming: `feature/<topic>`, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`.
- **PRs and issues live only on the fork `TUBS-IVS/eqasim-bs`.** The GitHub web UI defaults
  a PR base to the upstream — always switch it, or just use the alias:
  `git config alias.pr '!gh pr create --repo TUBS-IVS/eqasim-bs --base main'`.
- See open vs merged branches at a glance: `git branch-status`.
- Auto-delete-on-merge is enabled; `main` requires a PR (admin override retained).

## Issue-first for newly discovered work

When a new feature/gap/idea surfaces mid-work, **propose it first**, then open a GitHub
issue (`feature` / `bug` / `decision` template) **in `TUBS-IVS/eqasim-bs`**. Nothing
incidental gets forgotten. Decisions graduate into an ADR in `docs/DECISIONS.md`.

## Non-negotiable rules (summary — full text in CLAUDE.md)

- **English** for all code/comments/docs/commit messages; German only in chat.
- **No silent fallbacks**: every fallback logs its primary-vs-fallback rate, and the primary
  path is tested. A high fallback rate is a bug signal.
- **No invented reference values**: a target/reference must trace to a committed source,
  else it is labelled `ASSUMPTION`. **Convergence is not validation.**
- **All parameters configurable** (no hard-coded paths/seeds/thresholds/CRS); **all outputs
  traceable**; **all inputs validated** (fail early with a clear message).
- **Never push without explicit confirmation.**
- Units explicit in names (`distance_meters`, `travel_time_seconds`); CSV columns snake_case.

## Running a smoke

```powershell
python scripts/run_synpp.py configs/fixtures/config_local_braunschweig.yml   # 1% local, conda env eqasim
```

See [docs/ONBOARDING.md](docs/ONBOARDING.md) for the full environment + run guide.

### After touching a PopulationSim control

Two layers, cheapest first (issue #282). Both are required before claiming a control works.

**1. Specification checks — seconds, no PopulationSim, no data:**

```powershell
python -m pytest tests/test_control_fit_smoke.py -q
```

These run against the ACTIVE catalog and registry and catch the defects a real run would only
reveal hours in: categories that do not partition the seed universe (a person counted twice or
dropped from a total that is supposed to be partitioned), a `census_source` column that is
neither in the cell parquet nor produced by the aggregation map (PopulationSim fails with
`<field> not in index`), and a target table that is not normalised or misses a Kreis. The
reusable checks live in `braunschweig.analysis.population_validation.control_fit_smoke`.

**2. Numerical smoke — one Kreis, full control set:**

```powershell
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/smoke_kreis_control_fit.yml
```

Do NOT reach for a low `sampling_rate` to make this cheap: under `popsim_mid` the balancing
covers the FULL population regardless of the rate, which only trims the MATSim extract
afterwards. Shrinking the REGION is what reduces the work. The overlay runs Braunschweig only
and stops at `data.census.filtered` (population synthesis, no locations/trips/MATSim).

**Delete the smoke cache before any code-change comparison.** synpp hashes only a stage
module's own source; the helper surface rides on each stage's `validate()` token, and that
token deliberately stops one import level deep (`docs/codebase/notes/synpp-helper-hash-audit.md`).
A behaviour change in a module outside the token does NOT devalidate a warm cache, so an A/B
on one silently compares two identical populations -- observed live on 2026-08-19, when the
licence-floor and W_ZWECK fixes left the popsim stage hash byte-identical
(`docs/runs/smoke-control-fit-03101-v2-2026-08-19.yml`). `rm -rf` the run's cache directory
(never the shared `cache_shared` store) between the two arms of any code-change A/B.
