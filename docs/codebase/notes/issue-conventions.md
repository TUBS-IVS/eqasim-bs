# Issue conventions (TUBS-IVS/eqasim-bs)

GitHub issues on the fork are the ONLY backlog (CLAUDE.md). This note is the one home
for how an issue is named, typed, labelled and opened. The Project board is a mirror
that `/close` keeps in sync; nobody has to open it to understand an issue.

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

The step is the coarse axis one thinks in; the area is the registry axis that links an
issue to its stage and feature records. Area determines step:

| step | areas |
|---|---|
| `base-data` | `spatial`, `infrastructure` |
| `population` | `population`, `attributes`, `fleet`, `home` |
| `generation` | `behavior` |
| `distribution` | `work`, `education`, `secondary` |
| `mode-choice` | (`behavior`/`matsim` issues about mode choice, parking, ASCs) |
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

## Title rule

`<stage or attribute>: <what is wrong / what is produced / what is decided>` — at most
about 90 characters, no `[bug]`-style prefix (the type badge shows it), no `(#357)` parent
reference (sub-issues show it), no label words. Numbers only when measured; the source of
every number goes in the body.

- Bug: state the defect. `PT subscription: has_pt_subscription and pt_subscription_type drawn independently (0.86 % disagree)`
- Feature/Task: the outcome. `SrV distance calibration, layer 1: per-home-Kreis inter-Gemeinde commute friction`
- Analysis: `Validate … against <reference>` / `Measure …`.
- Decision: the question or the alternatives. `Home->home round trips (2.6 %): virtual destination or zero-distance legs`

## Opening an issue from a session

```
gh issue create --repo TUBS-IVS/eqasim-bs --type Feature \
  --label step:generation --label area:behavior --label data-quality \
  --title "<stage>: <outcome>" --body-file issue.md
gh issue edit 360 --repo TUBS-IVS/eqasim-bs --parent 357     # programme step
gh issue list --repo TUBS-IVS/eqasim-bs --label prio:now
gh issue list --repo TUBS-IVS/eqasim-bs --type Bug --label step:distribution
```

The five issue forms under `.github/ISSUE_TEMPLATE/` set the type and ask for the
`Stages:` field; `step:`/`area:` labels are added by hand, `prio:` only at triage by the
model owner. Programmes (several dependent steps) are a parent issue with sub-issues.
Milestones are reserved for time-boxed waves with a defined end.

## Triage check (part of `/close`)

Every open model issue has: a type, one `step:` label, at least one `area:` label, a
`Stages:` line. `prio` may be absent (= not yet triaged) but the untriaged list is
reviewed at every close. Closed issues keep their type and labels; their titles are not
rewritten.

## Retired

Labels `bug`, `enhancement`, `decision`, `question`, `documentation`, `good first issue`,
`help wanted`, `invalid`, `wontfix`, `duplicate` (types and native close reasons replaced
them). The `[bug] ` / `[feature] ` title prefixes. Migration applied 2026-09-07 to all 218
issues (types, step/area labels; open titles rewritten, closed titles kept).
