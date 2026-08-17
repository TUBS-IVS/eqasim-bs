# ADR-0083 · 2026-08-17 · One canonical Gemeinde normaliser + a Gebietsstand crosswalk for the KBA Gemeinde tilt (issue #277 merge)

- **Status:** active
- **Context:** Issue #161 (the Gemeinde-tilt join missing ~69 % of vehicles because
  the KBA FZ 27.17 sheet is ASCII-transliterated and abbreviated while the
  household home Gemeinde comes from the BBSR RegioStaR `name_20` reference) was
  fixed TWICE, independently: on `main` as
  `fleet_sampling_de.normalize_gemeinde_name` (commit `fbb86da8`, a fixed
  suffix-token list `STADT|ST|FLECKEN`, NaN-safe) and on
  `feature/fleet-quality-and-data` as `fleet_sampling_de.normalize_gemeinde`
  (uppercase + umlaut fold, drop everything after the first comma, drop
  parentheticals, not NaN-safe). Merging the fleet branch forced a choice: the
  reference side, the population side and the `extract_gemeinde_ev` extractor MUST
  use the identical function or the join silently degrades to the Kreis fallback.
- **Decision:**
  1. **One canonical function**, keeping `main`'s name and contract
     (`normalize_gemeinde_name`, NaN/`None` → `""`) and the branch's measurably
     better rules: drop a parenthetical qualifier, then drop everything from the
     first comma on (which covers the long-form suffixes RegioStaR uses, e.g.
     `", BERG- UND UNIVERSITAETSSTADT"`, not just `STADT|ST|FLECKEN`). The branch's
     `normalize_gemeinde` is deleted; the extractor, both key builders and the
     tests now call the single function.
  2. **A *gemeindefreies Gebiet* normalises to `""`, i.e. it is excluded from the
     join** rather than folded onto the neighbouring town's key. RegioStaR lists
     10 such areas in the ZGB (`"HARZ (LANDKREIS GOSLAR), GEMFR. GEBIET"`,
     `"SCHOENINGEN, GEMFR. GEBIET"`, ...); they are unpopulated forest / training
     areas and appear in NO KBA Gemeinde table.
  3. **A `(kreis_ags5, name) → successor name` Gebietsstand crosswalk**
     (`GEMEINDE_GEBIETSSTAND_CROSSWALK` / `apply_gebietsstand_crosswalk`) maps
     population labels whose Gemeinde was merged AFTER the population's
     Gebietsstand 2020 but BEFORE the KBA reference vintage. One entry today:
     Hahausen, Flecken Lutter am Barenberge and Wallmoden (Kreis Goslar) →
     Langelsheim. Every crosswalked car is counted and logged separately from
     primary/fallback.
- **Rationale:** measured, not assumed. Applying each candidate to the real
  vocabularies — the 113 keys of `kba_gemeinde_private_bev.csv` against the 126
  RegioStaR `name_20` labels of the 8 ZGB Kreise — gives:

  | variant | populated Gemeinden matched | false matches |
  |---|---|---|
  | `main` fixed suffix-token list | 110/116 (94.8 %) | 0 |
  | branch comma-drop | 113/116 (97.4 %) | **3** |
  | decision 1+2 | 113/116 (97.4 %) | 0 |
  | decision 1+2+3 | **116/116 (100 %)** | 0 |

  The branch variant's three false matches are exactly the gemeindefrei case:
  `"SCHOENINGEN, GEMFR. GEBIET"`, `"HELMSTEDT, GEMFR. GEBIET"` and
  `"MARIENTAL, GEMFR. GEBIET"` collapse onto the neighbouring town's key and
  would silently inherit that town's EV tilt.
  The remaining three misses under decision 1+2 were **not** a suppression or a
  normalisation defect but a municipal merger: the Samtgemeinde Lutter am
  Barenberge member communities were incorporated into Stadt Langelsheim on
  1 November 2021 (Niedersächsische Staatskanzlei, press release
  "Mitgliedsgemeinden der Samtgemeinde Lutter am Barenberge fusionieren mit der
  Stadt Langelsheim"). The data corroborates the statute independently: RegioStaR
  (Gebietsstand 2020) carries `03153006 Hahausen`, `03153007 Langelsheim`,
  `03153009 Lutter am Barenberge`, `03153014 Wallmoden`, while every KBA period
  from 2023.01 to 2026.04 carries a single **new** `03153019 Langelsheim` and none
  of the four predecessors. The crosswalk therefore records an administrative
  fact, not a tuned parameter — adding an entry not backed by a merger statute
  would fabricate a reference assignment (CLAUDE.md, "No invented reference
  values").
- **Consequences:** the Gemeinde EV tilt now reaches a reference row for **every
  populated ZGB Gemeinde**. Households in Hahausen, Lutter am Barenberge and
  Wallmoden receive Langelsheim's EV share instead of the Kreis Goslar average
  (they are, administratively, part of Langelsheim). `main`'s tilt results change
  for those three Gemeinden and for the parenthetical/long-suffix cases
  (Müden (Aller), Veltheim (Ohe), Clausthal-Zellerfeld), which previously fell
  back to the Kreis share; the fallback-rate log gains a crosswalk line. The
  crosswalk is a module constant, not a config key: it encodes statute, and a
  per-run override would invite exactly the invented assignments the rule forbids.
  Adding a future merger means one dict entry plus its source.
- **Evidence:** `tests/test_gemeinde_normalize.py` (10 assertions: umlaut/suffix
  folding, long-form suffix, parenthetical, both-sides key equality, gemeindefrei
  exclusion incl. the explicit "must not equal the town's key" pair, NaN/None,
  crosswalk hit, crosswalk no-op in another Kreis);
  `tests/test_extract_kba_gemeinde_ev.py::test_gemeinde_norm_matches_normalize_gemeinde_name`
  pins extractor and population side to the identical function. The coverage
  table above was produced with
  `scripts/measure_gemeinde_join_coverage.py` against the committed
  `kba_gemeinde_private_bev.csv` and `eqasim-data/data/regiostar/regiostar_referenzdatei.xlsx`.
- **Alternatives rejected:** (a) Keep `main`'s function unchanged — 6 populated
  Gemeinden stay on the Kreis fallback for a purely lexical reason. (b) Keep the
  branch's function unchanged — buys those 3 Gemeinden at the price of 3 silent
  false matches, and is not NaN-safe. (c) Join on AGS-8 instead of names — the
  structurally correct fix, but the FZ 27.17 source carries no Gemeinde AGS at
  all (only `kreis_ags5` + name), so it cannot be keyed that way; the 2026
  `kba_gemeinde_ev` source does carry `Gemeindeschlüssel` and could migrate to an
  AGS join independently (recorded as follow-up work, not done here). (d) Treat
  the three merged Gemeinden as a data gap and leave them on the Kreis fallback —
  defensible but demonstrably worse, since the reference DOES cover them under
  the successor's name.
- **Issue / PR:** #277
