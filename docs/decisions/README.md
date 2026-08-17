# Architecture decision records (ADRs)

One file per decision: `ADR-NNNN-<slug>.md`. These files ARE the authoritative
record of every substantive scientific/architectural decision (kept, rejected,
superseded). The browsable index is generated to
[`docs/generated/DECISIONS.md`](../generated/DECISIONS.md) — never edit that
file; edit or add the record here and rebuild
(`python -m braunschweig.documentation build`).

## How these files came to be

Until 2026-08-13 all records lived in one monolithic `docs/DECISIONS.md`
(archived verbatim at
[`docs/archive/DECISIONS_monolith_2026-08-13.md`](../archive/DECISIONS_monolith_2026-08-13.md)).
ADR-0077 split it into these per-record files, preserving every body
byte-for-byte; only the heading level was normalized to `#`. The three
organically grown heading forms (`# ADR-NNNN · date · title`,
`# ADR-NNNN — title`, `# ADR-NNNN — title (date, PR ...)`) were deliberately
NOT homogenized — per the project's no-invented-history rule the record text is
evidence, not editable prose.

## Numbering notes (traceable, not invented — carried over from the monolith)

> ADR-0051 was reserved for the fleet branch record and stays PERMANENTLY UNUSED:
> when `feature/fleet-quality-and-data` merged (issue #277, 2026-08-17) its two
> drafted records were renumbered to the next free numbers, ADR-0081 (drafted as
> ADR-0050, which collides with main's TAZ-friction ADR-0050) and ADR-0082
> (drafted as ADR-0051); both renumberings are recorded in those records' own
> headings and bodies. ADR-0052/0053/0054 carry no date field
> in their own body text (unlike every other entry, which states one); their index
> date is `n/a` rather than inferred from surrounding entries, per the project's
> "no invented reference values" rule. ADR numbering in the body is not always
> chronological (see the numbering-collision notes at/near ADR-0052, ADR-0056,
> ADR-0067, and ADR-0068); the generated index is sorted by ADR number for lookup,
> not by date. ADR-0074 was originally numbered 0071 on `backup/pm-0d117f7` and
> renumbered when it collided with origin's ADR-0071 (recorded in its heading).

## What an ADR is here

Each entry records one substantive decision: the **context** (the problem), the
**decision** (what was chosen), the **rationale** (why — grounded in a committed
source), the **consequences** (what it enables or costs), and **evidence** (at
least one committed reference: a commit hash, a merged PR number, a spec/plan
path, or a feature doc). Per the project rule in `CLAUDE.md` ("no invented
reference values"), every rationale and number traces to a committed source
actually read; where the *why* is not recoverable from the record, the entry
says so rather than guessing.

## Status vocabulary

- **active** — the decision is in force on `main`.
- **superseded by ADR-NNNN** — replaced by a later decision (the entry stays for
  traceability).
- **rejected** — tried or designed and deliberately not adopted (the "why we did
  NOT do it" is recorded so it is not re-attempted).

## Adding a new ADR

1. Take the next free number (check the generated index; do not renumber).
2. Create `ADR-NNNN-<slug>.md`, heading `# ADR-NNNN · YYYY-MM-DD · Title`,
   with Status/Context/Decision/Rationale/Consequences/Evidence sections.
3. Rebuild the generated docs and run the documentation check.
