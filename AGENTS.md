# Agent Bootstrap — eqasim-bs (Braunschweig)

> Read this file first. It is the single entry point for any AI session
> working on this repository. It points at the longer-form docs rather than
> duplicating them.

## Mission in one paragraph

`eqasim-bs` synthesises a Braunschweig (ZGB-8) population for the MATSim
agent-based transport simulator. It started as a fork of
[eqasim-bavaria](https://github.com/eqasim-org/eqasim-bavaria); that fork has
since been completed — the `bavaria/` directory no longer exists, and
region-neutral code lives in `eqasim_common/` with the Braunschweig-specific
pipeline in `braunschweig/`. This is now a mature, actively developed research
pipeline, not a refactor-in-progress; treat `CLAUDE.md` (project root) as the
authoritative style/conventions guide and `PROJECT_STATUS.md` as the
authoritative "what exists, what state is it in" source.

## Read order for any new session

1. [`CLAUDE.md`](CLAUDE.md) — project conventions, coding rules, git policy; **read this in full**.
2. [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — live state, feature matrix, branch/PR map. **First stop** for "what exists / where / how far along".
3. [`PROJECT_BACKLOG.md`](PROJECT_BACKLOG.md) — the ranked, open-work-only backlog.
4. [`docs/DECISIONS.md`](docs/DECISIONS.md) — ADRs (decision log with rationale).
5. [`docs/codebase/`](docs/codebase/) — architecture/onboarding (`STACK`, `STRUCTURE`, `ARCHITECTURE`, `CONVENTIONS`, `INTEGRATIONS`, `TESTING`, `CONCERNS`). **Several of these carry explicit staleness banners** (dated mid-2026) — cross-check any specific claim against `PROJECT_STATUS.md` and the actual code before relying on it.
6. `docs/knowledge/` (if present, gitignored) — per-project on-demand session notes.

`SESSION_LOG.md` (gitignored, local) has the chronological session history if one exists on this machine.

## Environment

- OS: this session may run on Windows (PowerShell) or Linux (server); commands below are illustrative, not OS-exhaustive.
- Python: `environment.yml` pins `python=3.10.10`.
- **Known divergence (tracked, not yet resolved):** `environment.yml` declares `name: ile-de-france` (inherited from the upstream fork point) and CI activates that name, while README/CLAUDE.md tell contributors to use an env named `eqasim`. Either name works as long as it's created from `environment.yml`; see `docs/codebase/CONCERNS.md` for the open question of which name should become canonical.
- Java MATSim/eqasim side: built from the `eqasim-java-bs` fork (external sibling checkout), downloaded/cached by synpp under `eqasim-data/cache_*/matsim.runtime.eqasim*`. See `docs/codebase/STACK.md` for the current pinned version and JDK requirement.

## Repository conventions enforced now

- All comments and docstrings in **English**. German Zensus/BA/MiD field names stay as-is for traceability.
- Full coding/style/architecture rules live in `CLAUDE.md` — do not duplicate them here; read that file.
- Stage names in `configs/*.yml` are dotted Python module paths; `aliases:` remap upstream eqasim stages to `braunschweig.*` / `eqasim_common.*` overrides. See `docs/codebase/ARCHITECTURE.md` for the alias map and `docs/codebase/STRUCTURE.md` for the config family split (`configs/base_bs.yml` + `configs/overlays/*` for composed production runs vs `configs/fixtures/*` for standalone dev/test configs).

## Day-to-day commands

```powershell
# Smoke run (single fixture config)
python scripts/run_synpp.py configs/fixtures/config_local_braunschweig.yml

# Composed production / all-features run: fixed base + per-scale overlay
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml

# Test suite
pytest tests/ -v
```

See `docs/codebase/STRUCTURE.md` for the full config inventory and `docs/codebase/TESTING.md` for how the test suite is organized and which tests are opt-in/skipped by default.

## Decisions

There is no separate refactor decision log any more — every architectural or
scientific decision (including the historical fork-cleanup decisions) is
recorded as an ADR in [`docs/DECISIONS.md`](docs/DECISIONS.md), with a
one-line-per-ADR index at the top of that file. Add a new ADR there when you
make a decision worth remembering; do not invent a parallel decision file.

## Hard rules for AI sessions

1. Never run `git push --force`, `git reset --hard` against shared branches, or `rm -rf` on `eqasim-data/` without explicit user confirmation.
2. Never run `git add -A` from the repository root without first checking `git status` — the synpp cache contains nested git repos and can contain large/derived/restricted data.
3. Never bypass `pre-commit` or test gates with `--no-verify`.
4. Never commit anything under `eqasim-data/` other than what `.gitignore`'s explicit allow-list permits (small derived reference CSVs and the `DOWNLOAD_CHECKLIST*.md` files) — see the policy comment in `.gitignore`. MiD-derived microdata and other restricted inputs must never be committed.
5. Never modify the Java MATSim sources from this repo. They live in the external `eqasim-java-bs` fork and are only cached (read-only) here.
6. Never push to any remote, and never open a GitHub issue/PR, without explicit per-action user confirmation — see `CLAUDE.md` "Git and version control".
7. Always end a working session per `CLAUDE.md`'s "Mandatory at `/close`" checklist (update `PROJECT_STATUS.md`/`PROJECT_BACKLOG.md`/`SESSION_LOG.md`, ADRs, RUNS.md where applicable).
