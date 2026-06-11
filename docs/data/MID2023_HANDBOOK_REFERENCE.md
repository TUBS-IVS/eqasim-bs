# MiD 2023 — working reference (constraints we rely on)

Distilled from the official **MiD 2023 Handbuch zur Datennutzung** (infas, DLR, IVT,
infas 360, im Auftrag des BMV, Bonn/Berlin, August 2025) — local copy at
`popsimprep/inputs/MiD2023/MiD2023_B1_Codebook_HandbuchDatennutzung/MiD2023_HandbuchZurDatennutzung.pdf`,
codebook `MiD2023_Codepläne_B1_Standard_v1.1.xlsx`. Online:
https://www.mobilitaet-in-deutschland.de . We use the **B1** (faktisch anonymisiert)
package. This file is the project's quick reference so the constraints below are not
forgotten; cite the handbook chapter/table for anything load-bearing.

## 1. Datasets and case counts (Handbuch Tab. 1)

Seven sub-datasets (gesamt = Basisstichprobe + Vertiefungsstichproben):
`Haushalte` 218,101 · `Personen` 420,979 · `Wege` 1,087,393 · `Autos` 290,930 ·
`Reisen` 69,788 · `Tagesreisen` 67,231 · `Etappen` 94,337 (only multi-leg trips).
Keys: `H_ID` (household), `P_ID` (person within HH), `W_ID` (trip), `HP_ID`.
Naming convention (Kap. 5.1): **UPPERCASE** = raw survey variable; **lowercase** =
analytical (derived) variable (e.g. `oek_status`, `hheink_gr1`, `haushaltstyp`,
`hvm`). Household-member columns end `_1.._8`; multiple-response end `_A,_B,...`.

CSV separators differ: `MiD2023_Personen.csv` is **comma**-separated; `MiD2023_Wege.csv`
is **semicolon**-separated. Always check the header before `read_csv`.

## 2. Missing-value coding (Handbuch Kap. 5.1, Tab. 2-3) — CRITICAL

Only POSITIVE codes are used for missing; each code has exactly one meaning. Two classes:

### Antwortbedingt (random item non-response)
| Code | Meaning |
|---|---|
| `9, 99, 999, ...` | keine Angabe (refused / "kann/möchte nicht beantworten") |
| `94, 994, 9994, ...` | unplausibler Wert |
| `95, 995, 9995, ...` | Wert nicht zu berechnen / nicht zuzuordnen |
(Index digit 9 is prefixed to the field width.)

### Designbedingt (structural / "qualified missing") — 3-digit, FIRST digit = type
| 1st digit | Type | Example code → meaning |
|---|---|---|
| 1 | **Modul** | 101/104/110 → Modul nicht erhalten (asked only to a module subset) |
| 2 | Interviewart | 202 → im PAPI nicht erhoben; 206 → Erwachsener ab 14 (Proxy/Stellvertreter) |
| 3 | Haushaltsgröße | 308 → Person 8 im HH nicht vorhanden |
| 4 | Soziodemographie | 402 → Kind unter 14; 404 → Nicht-Erwerbstätige/r; 407 → keine Info zur Tätigkeit |
| 5 | Mobilitätseigenschaften | 502 → HH ohne Auto |
| 6 | Stichtag außer Haus | 602 → Person ohne Außer-Haus-Aktivität |
| 7 | Wege | 701 → bei rbW (regelm. berufl. Wegen) nicht erhoben |
| 8 | Mobilität am Stichtag | 804 → Person mit unbekannter Mobilität |
| 9 | Etappen | 904 → Etappenerfassung nicht vorgesehen |

**Key fact (Kap. 6.3):** "Die MiD ist durch eine hohe Anzahl an Missing-Kategorien
gekennzeichnet. Hierbei handelt es sich **nur zu einem sehr geringen Anteil um item
non response**. Der größte Anteil beschreibt **designbedingt fehlende Werte**." So most
"missing" is STRUCTURAL (children, non-employed, no-car, PAPI, proxy, module), not refusal.

## 3. Modules, proxy interviews, Grundgesamtheit (Kap. 6.3) — the COVERAGE rule

Module questions (`1xx` design-missing) were asked ONLY to the module subset → high
"missing", LOW coverage. Verified examples (ungewichtet, n=420,979):
- **Online-Shopping**: only 25.9% asked (104 Modul = 50.5%, 202 PAPI = 17.1%, ...).
- **Homeoffice** (`hoff1`): only 16.5% asked (110 Modul = 49.5%, 404 Nicht-Erwerbst. = 10.1%).

→ **Do NOT synthesise module / subset-only variables population-wide.** Use a variable
only for its **Grundgesamtheit** (the subset it was validly asked to). Within that
Grundgesamtheit the handbook says the available answers "kann ... vereinfacht
angenommen werden, dass [sie] für alle Befragten der Grundgesamtheit gelten und dem
wahren Wert sehr nahe kommen" (Kap. 6.3) → and Pkw-Verfügbarkeit example (Kap. 6.3 Tab.6):
the no-answer groups (keine Angabe + Proxy) are redistributed proportionally to the
answered distribution.

Three questions to ask of EVERY variable before use: (1) Wem wurde die Frage gestellt?
(2) Auf welche Grundgesamtheit bezieht sich das Ergebnis? (3) Welcher Teil der
Grundgesamtheit wurde nicht gefragt?

## 4. Imputation by MiD (Kap. 4.2)

MiD itself statistically imputes missing/implausible values for the three core mobility
variables — **Hauptverkehrsmittel, Entfernung, Wegedauer** → use the imputed variants
`hvm_imp`, **`wegkm_imp`**, `wegmin_imp1/2`. Household net income missings are also
imputed. Other prep: immobile-person adjustment (model-based), Nach-Hause-Wege added for
PAPI, double-reported regelmäßige berufliche Wege removed, begleitete Wege deduplicated.

Analytical (derived, lowercase) variables include `haushaltstyp`, `oek_status`
(ökonomischer Status), mono/multimodal groups, KBA car classification — these are
clean derived attributes, good to use.

## 5. Weighting & Hochrechnung (Kap. 6.1-6.2) — CRITICAL for the donor logic

Grundgesamtheit = Wohnbevölkerung ab 0 Jahren. Each dataset has a Fallzahl-normed
weight (mean ≈ 1); HH/Person/Wege also have a Hochrechnung factor:

| Level | Gewicht | Hochrechnung | → total |
|---|---|---|---|
| Haushalte | `H_GEW` | `H_HOCH` | 40.6 M HH (×185.95) |
| Personen | `P_GEW` | `P_HOCH` | 83.5 M persons (×198.24) |
| Wege | `W_GEW` | `W_HOCH` | 249 M trips/day |
| Autos | `A_GEW` (= HH weight) | — | |
| Reisen/Tagesreisen/Etappen | `R_GEW`/`TR_GEW`/`ET_GEW` | — | (Etappen use Wege weight) |

- Household weight calibrated on: **Bundesland, Haushaltsgröße (1/2/3/4/5+),
  Wohnsituation (Miete/Eigentum), Monat, Wochentag**.
- Person weight calibrated on: **Bundesland, Erwerbsstatus / Haupttätigkeit,
  Schulabschluss, Elementargebiet, Geschlecht, Altersgruppe**.
- Wege weight = person weight × **Hebefaktor** (covers mobile non-reporters + trips
  beyond the 12 (CATI/CAWI) / 8 (PAPI) detailed ones). Verkehrsleistung:
  `W_GEW_PKM = wegkm_imp × W_GEW`, `W_HOCH_PKM = wegkm_imp × W_HOCH`.
- Tagesstrecke (Kap. 6.2) = Σ `wegkm` per person × Hebefaktor (already in the person
  dataset). For per-mode/purpose splits, multiply by the Hebefaktor yourself.

Caution (Kap. 6.1): for small/specific groups (e.g. 80+ with licence, rural MPH),
compare weighted vs unweighted before interpreting.

## 6. Additional context variables (Kap. 4.2)

Appended from BBSR (**regionalstatistischer Raumtyp / RegioStaR**), Destatis (politische
Gemeindegrößenklasse), infas 360 (ÖPNV-Qualität am Wohnort). RegioStaR variants present:
`RegioStaR2/4/5/7/17`, `RegioStaRGem5/7`.

## 7. How WE use this in popsim_mid (links to the integration design)

- **Donor weighting:** popsim_mid uses the MiD household as a weighted donor; `H_GEW`
  (and the calibration margins Bundesland/HH-size/tenure/month/weekday) is what makes the
  weighted donor pool representative → attribute marginals match MiD by construction
  (the `alpha` donor-inheritance strategy in the integration spec).
- **Trip distance:** use **`wegkm_imp`** (MiD's own imputed, full-coverage distance);
  `euclidean_distance = wegkm_imp / 1.3` (ENTD detour convention).
- **Missing-data policy (integration spec Section 6):** decode by the Tab. 2/3 scheme —
  structural `2xx-9xx` mapped deterministically by type (402 child → no licence/ticket,
  502 → 0 cars, 404 → non-employed); `1xx` module variables fail the **coverage gate**
  (not synthesised population-wide); only `9/99` random non-response (tiny) is imputed
  from comparable respondents and logged.
- **Attributes we use are NOT module variables** and pass the gate: `P_BKAT` (occupation,
  ~0% k.A., all employed), `P_FSCHEIN`, `P_FKARTE`, `oek_status`, `hheink_gr1` (imputed),
  `P_TAET`, `erwerb` — verified on n=420,979.
- **Licensing:** raw MiD is local-only / scientific-use; never commit microdata. The
  derived aggregate reference CSVs under `eqasim-data/data/braunschweig/mid/` are the
  committable distillations (see project CLAUDE.md).

## Regenerate / verify
Coverage and code distributions can be re-checked with `pdfplumber` on the handbook PDF
and `pandas` value_counts on the local CSVs (Personen = comma-sep, Wege = semicolon-sep).
