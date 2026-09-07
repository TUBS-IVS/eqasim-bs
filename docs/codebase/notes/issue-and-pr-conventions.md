# Issue and PR conventions (TUBS-IVS/eqasim-bs)

GitHub issues on the fork are the ONLY backlog (CLAUDE.md). This note is the one home for
how an issue and its PR are named, typed and labelled. The Project board is a mirror that
`/close` keeps in sync; nobody has to open it to understand a work item.

## Four dimensions, one home each

| Dimension | Home | Cardinality | Values |
|---|---|---|---|
| Type (kind of work) | native GitHub **issue type** | exactly one | `Bug`, `Feature`, `Analysis`, `Decision`, `Task` |
| Model step (four-step view) | label `step:*` | exactly one for model issues, none for tooling/docs | `base-data`, `population`, `generation`, `distribution`, `mode-choice`, `assignment` |
| Model area (registry view) | label `area:*` | one or two | the 15 `MODEL_AREAS` from `braunschweig/documentation/schema.py` plus `data`, `tooling`, `docs` |
| Priority (when) | label `prio:*` | at most one; absent = untriaged | `now`, `next`, `later` |

Flags (non-exclusive, sparse): `upstream` (port/sync from eqasim-org), `data-quality`
(input-data correctness, orthogonal to area), `blocked` (waits on a server run, data or a
decision; the first body paragraph says which), `parked` (a branch is parked; the issue
names branch and commit). `history` and `rejected` stay frozen on the closed records that
carry them; new issues never get them.

Status is NOT a label: open/closed plus `prio` plus `blocked`/`parked` is the whole state.

### Types

- **Bug** — a wrong, non-reproducible or scientifically indefensible result, including a
  silent fallback that fires for most items.
- **Feature** — a model extension or data integration; flag-gated, OFF path byte-identical.
- **Analysis** — a measurement or validation against a committed reference. Produces
  evidence (a run manifest under `docs/runs/`), does not change the model. Convergence is
  not validation.
- **Decision** — an ADR candidate; ends as `docs/decisions/ADR-NNNN-*.md`.
- **Task** — everything else with a defined end: refactoring, server run, config wiring,
  tooling, documentation.

Validation is a *type* (Analysis) at a *step*, never a step of its own: a commute-distance
validation is `Analysis` + `step:distribution`.

### Step vs area

The step is the coarse axis one thinks in; the area is the registry axis that links a work
item to its stage and feature records. Area determines step:

| step | areas |
|---|---|
| `base-data` | `spatial`, `infrastructure` |
| `population` | `population`, `attributes`, `fleet`, `home` |
| `generation` | `behavior` |
| `distribution` | `work`, `education`, `secondary` |
| `mode-choice` | mode choice, parking and ASC issues (`behavior` / `matsim`) |
| `assignment` | `matsim` |

`cordon` and `freight` are demand segments and lie across steps; `analysis` and
`validation` take the step of the thing they measure. `data`, `tooling`, `docs` carry no
step.

### The stage is a body field, not a label

115 stages would be 115 labels. Every model issue names its stage(s) twice: as the object
at the front of the title, and as the first body line

```
**Stages:** `synthesis.population.trips`, `braunschweig.popsim.completed_donor`
```

using the registry ids (`docs/registry/stages/<id>.yml`). `unknown` is a valid value and
is fixed at triage. Search: `gh issue list --search "synthesis.population.trips in:body"`.

## Issue title rule

`<stage or attribute>: <what is wrong / what is produced / what is decided>` — at most
about 90 characters, no `[bug]`-style prefix (the type badge shows it), no `(#357)` parent
reference (sub-issues show it), no label words. Numbers only when measured; the source of
every number goes in the body.

- Bug: state the defect. `PT subscription: has_pt_subscription and pt_subscription_type drawn independently (0.86 % disagree)`
- Feature/Task: the outcome. `SrV distance calibration, layer 1: per-home-Kreis inter-Gemeinde commute friction`
- Analysis: `Validate … against <reference>` / `Measure …`.
- Decision: the question or the alternatives. `Home->home round trips (2.6 %): virtual destination or zero-distance legs`

## PRs: same axes, one kind prefix

A PR is a *change*, so it needs no type field of its own — the branch prefix carries the
kind, and the PR inherits the issue's `step:`/`area:` labels so both lists filter the same
way. `prio:` never goes on a PR.

| Issue type | Branch prefix | Example |
|---|---|---|
| Bug | `fix/` | `fix/i344-chainsolver-worker-death` |
| Feature | `feature/` | `feature/i329-pt-never-group` |
| Analysis | `analysis/` | `analysis/i369-day-structure-vs-srv` |
| Decision | `docs/` (the ADR is the deliverable) | `docs/adr-0079-donor-attributes-255` |
| Task | `chore/`, or `test/` for a test-only change | `chore/git-working-hygiene` |

**Branch:** `<prefix>/i<issue>-<slug>`, lowercase, hyphenated, and it is also the worktree
name (`.claude/worktrees/<slug>`). One worktree per task, branched off `origin/main`.

**PR title:** what the change does, imperative or nominal, at most about 90 characters,
ending in `(#NN)` or `(closes #NN)`. No `[fix]` prefix, no bare `fix` / `update` / `changes`.
Good: `Survive a killed chainsolver shard worker instead of waiting forever (#344)`.

**PR body:** the template's sections, and the same `**Stages:**` line as the issue, so a
reviewer sees which registry stages the diff touches without reading the diff. `Closes #NN`
is mandatory whenever an issue exists — a PR without an issue is only for hygiene commits
that the issue-first rule does not cover.

```
git pr --label step:generation --label area:behavior \
  --title "<what the change does> (closes #NN)" --body-file pr.md
```

(`git pr` is the alias pinning base `main` on the fork; extra flags pass straight through
to `gh pr create`.) Merging is the user's action, never ours.

## Triage check (part of `/close`)

Every open model issue has: a type, one `step:` label, at least one `area:` label, a
`Stages:` line. `prio` may be absent (= not yet triaged) but the untriaged list is reviewed
at every close. Closed issues keep their type and labels; their titles are not rewritten.

```
gh issue list --repo TUBS-IVS/eqasim-bs --label prio:now
gh issue list --repo TUBS-IVS/eqasim-bs --type Bug --label step:distribution
gh issue list --repo TUBS-IVS/eqasim-bs --search "no:label -label:prio:now"   # untriaged
```

## Retired

Labels `bug`, `enhancement`, `decision`, `question`, `documentation`, `good first issue`,
`help wanted`, `invalid`, `wontfix`, `duplicate` (types and native close reasons replaced
them). The `[bug] ` / `[feature] ` title prefixes. Migration applied 2026-09-07 to all 218
issues (types, step/area labels; open titles rewritten, closed titles kept).
