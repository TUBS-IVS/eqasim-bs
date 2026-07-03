# Status presentation — eqasim-bs (Zwischenstand)

A self-contained, management-grade slide deck presenting the eqasim-bs
agent-based transport model of the Zweckverband Großraum Braunschweig (ZGB) to a
**non-technical research audience** (project group), plus the reproducible build
pipeline for it.

## What is here

| File | Purpose |
|---|---|
| `eqasim-bs-status-deck-de.html` | **The deliverable** — 48-slide deck, German, self-contained (all figures + TU logo inlined as base64). Open in any browser; `F11` for fullscreen. Navigate with ← → / scroll / the dots; every figure is click-to-zoom. |
| `deck_template.html` | Slide HTML with `__FIG_*__` / `__CAP_*__` placeholder tokens (no images inlined). |
| `fig_mapping.json` | Maps each token to a repo-relative image path (`figs/…`, `assets/…`) and a German caption. `drop_slides` can suppress whole slides. |
| `inline_figs.py` | Build step: reads `deck_template.html` + `fig_mapping.json`, base64-inlines the PNGs/SVG, writes the self-contained HTML. |
| `figs/*.py` | One matplotlib script per figure (dark "glow" style, Space Mono from `braunschweig/analysis/poster/fonts/`) plus a few prep/compute helpers. |
| `figs/*.png` | The rendered figures (committed so the deck rebuilds without re-running the model). |
| `assets/TUBraunschweig_SVG_Siegel.svg` | Official TU Braunschweig logo (title slide). |

## Rebuild the deck (from the committed PNGs)

```bash
cd braunschweig/analysis/presentation
python inline_figs.py deck_template.html fig_mapping.json eqasim-bs-status-deck-de.html
```

To change wording only, edit `deck_template.html` (structure/labels) or the
captions in `fig_mapping.json`, then re-run the command above.

## Regenerate a figure (needs the model outputs)

The figure scripts read **real run data**, not committed here — chiefly the
newest 100 % all-features PopulationSim run (`braunschweig_100pct_allfeat_popsim_*`
+ `analysis/population_validation/*` + raw MATSim `simulation_output/`, export
2026-06-30, commit `e1164cc`), the committed MiD/KBA reference tables under
`eqasim-data/data/braunschweig/`, the server popsim work dir
`popsim_work_allfeat_opt` (via `ssh felix`), and the official MiD 2023
Ergebnisbericht (Abb. 22, p. 82). Paths inside the scripts point at the machine
they were produced on and are kept for **provenance**; adjust them to a local
copy of the run to re-render, then `python figs/<name>.py`.

## Provenance & honesty notes (baked into the deck)

- **Everything is real data** — no invented reference values (per `CLAUDE.md`).
- Deviations are shown openly; caveats are framed as *upcoming* work (roadmap),
  not hidden.
- The MiD **district-level** controls (cars/bikes/licence/PT-ticket/employment-P9)
  are treated as small-sample **cross-checks, not calibration targets**; the
  synthesis is graded only against the official targets it actually calibrates to
  (Census 2022 / GENESIS / KBA). Our MiD **spatial-type** table is identical to
  the official report (≤ 0.5 pp).
- The 100 m control fit is reported in **absolute** terms (< ±1.3 households/persons
  per cell) because relative % explodes on cells with ~2-unit targets.

## Findings surfaced while building (tracked as GitHub issues, fork `TUBS-IVS/eqasim-bs`)

- **#96** — output `employed` flag not census-compatible for minors (inflates the
  regional employment rate ~7–9 pp; the 20+ base fits the census). Cross-linked to **#25**.
- **#97** — population-validation `household_size` control mixes a household-based
  synthetic count with person-based census targets.

Built 2026-07-03.
