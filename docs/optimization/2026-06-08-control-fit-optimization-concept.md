# Konzept: Control-Fit-Optimierung der Braunschweig-Synthese

**Datum:** 2026-06-08 · **Grundlage:** All-Features-25 %-Validierung (`output_bs_25pct_allfeat/analysis/population_validation/`)
**Ziel:** Die zwei verbesserungswürdigen Controls (`household_size`, `employment`) und den
„Rest" (`driving_license_type`) systematisch in den Griff bekommen — **datengetrieben, ohne Overfitting**.

## 0. Leitprinzipien (gelten für jede Maßnahme)
1. **Rausch vs. Bias trennen, bevor man kalibriert.** Eine Abweichung auf 25 %-Sample ist teils
   Sampling-Rauschen (∝ 1/√n, verschwindet bei 100 %), teils systematischer Bias (bleibt). Nur den
   **Bias** verfolgen — sonst kalibriert man Rauschen ein (Overfitting).
2. **Nur reale Daten als Ziel.** Kein erfundener Cross-Tab. Fehlt eine Zielgröße, wird sie als Datenlücke
   behandelt (beschaffen oder ehrlich descriptive lassen), nicht heuristisch ersetzt.
3. **Objektiv = das neue `population_validation`-Tool.** Es liefert pro Control SRMSE/|Δ| je Geo-Zelle —
   die messbare Zielfunktion der Optimierungsschleife (§4).
4. **Joint-Struktur erhalten.** Nicht jede Marginale hart raken, bis die Korrelationen kaputtgehen
   (z. B. age×size). Margen ergänzen, nicht übersteuern.

---

## 1. `household_size` (Gemeinde 7,0 pp — Kreis top)

**Diagnose:** Auf **Kreis**-Ebene exzellent; die 7 pp entstehen ausschließlich auf **Gemeinde**-Ebene.
113 Gemeinden, bei 25 % ~1.200 HH je Gemeinde, in 6 Größenklassen → kleine ländliche Gemeinden sind
multinomial verrauscht. Der per-Commune-Größen-Margin (Zensus 1000A-2081) IST aktiv.

**Hypothese:** überwiegend **Rauschen**, kein Bias. SRMSE 1,94 ist hoch, weil die Zielzahlen je
Gemeinde×Klasse klein sind (SRMSE = RMSE/mean(target) explodiert bei kleinen Zellen).

**Maßnahmen (in dieser Reihenfolge):**
- **M1.1 (zuerst, billig):** Bei 100 % re-validieren. Wenn die Gemeinde-Abweichung ~1/√4 = halbiert
  und der **mittlere signierte** Δ ~0 ist → bestätigt Rauschen, **keine Modelländerung nötig**.
- **M1.2 (falls Rest-Bias):** Diagnostik im Tool erweitern — Korrelation |Δ| vs. Zellgröße + signierter
  Δ je Gemeinde-Klasse. Zeigt, ob ein systematischer Größen-Shift bleibt (z. B. 1-P-HH über-/unterschätzt).
- **M1.3 (nur bei echtem Bias):** Post-Formations-Rake der Haushalts-Größenverteilung **je Gemeinde**
  gegen 1000A-2081 (largest-remainder, ohne Personen zu verlieren) als letzter Schritt der
  Haushaltsbildung. Erhält Kreis-Fit, korrigiert Gemeinde-Restbias.

**Aufwand/Impact:** M1.1 = 0 (nur re-validieren). M1.3 = mittel, nur falls nötig.

---

## 2. `employment` (5,3 pp) — der klarste echte Hebel

**Diagnose:** Die Erwerbsquote wird **nicht direkt** gegen MiD P9 geraked. `use_employment_margin`
nutzt einen **hh_size×employment-Proxy (Outer-Product)**, weil kein externer CSV vorliegt
(siehe Config-Kommentar). `employed` selbst stammt aus GENESIS 13111 — eine **andere Quelle/Definition**
als MiD P9 (Basis, Stichtag, Erwerbsbegriff). 5,3 pp ist daher teils **Quell-/Definitions-Bias**, nicht Rauschen.

**Maßnahmen:**
- **M2.1 (Kern):** Die **P9-Erwerbsquote je Kreis** (14+, schon im Tool als Ziel) als **expliziten
  IPF-Margin** einführen (statt des Proxys): `employed` per Kreis exakt auf die P9-Quote raken. Datenquelle
  existiert (`mid2023_P9.csv`). → bringt `employment` voraussichtlich in den „gut"-Bereich.
- **M2.2 (Konsistenz):** Definitions-Abgleich GENESIS-`employed` ↔ MiD-P9 dokumentieren; entscheiden,
  welche die Referenz ist (P9 ist die regionale MiD-Größe, die wir validieren → P9 als Ziel).
- **M2.3 (optional, später):** employment×age- oder employment×hhsize-Margin gegen P9-Untergliederung,
  **nur** wenn die P9-Tabelle das hergibt (kein erfundener Cross-Tab).

**Aufwand/Impact:** M2.1 = mittel, **hoher Impact** (echte Datenlücke geschlossen). **Priorität 1.**

---

## 3. `driving_license_type` (1,6 pp, „gut")

**Diagnose:** Im Output-CSV fehlt `license_type` → das Tool leitet aus dem Boolean ab (ja/nein, kein
keine_angabe). Modell selbst raked schon auf P17.1 (3-Margin-IPF). Plus ~1 pp struktureller Versatz
(P17.1-Basis 14+ inkl. BF17 vs. Synthese-Floor 18).

**Maßnahmen:**
- **M3.1 (billig):** `license_type` (+ `economic_status`, `housing_tenure`, `household_income_eur`) in den
  **eqasim-Output-CSV-Writer** aufnehmen → die volle 3-Kategorien-P17.1 wird direkt validierbar (statt
  Boolean-Ableitung). Erwartung: fällt unter 1 pp.
- **M3.2:** Den BF17-Versatz als bekannte, dokumentierte Strukturgröße akzeptieren (kein Fix nötig).

**Aufwand/Impact:** M3.1 = klein, niedriger Impact (kosmetisch + macht 3 weitere Attribute validierbar).

---

## 4. „Der andere Kram": geschlossene Kalibrierungs-Schleife (Infrastruktur)

Statt ad-hoc: eine **reproduzierbare Optimierungsschleife** mit dem Validierungs-Tool als Zielfunktion.

```
synthese  →  population_validation  →  controls_summary (SRMSE/|Δ| je Control×Geo)
   ↑                                              │
   └──── gezielte Margin-/Kalibrier-Anpassung ◄───┘   (nur Controls über Schwelle, nur mit realer Datenquelle)
```

- **M4.1:** Ein `scripts/diagnose_control_fit.py`, das `controls_long.csv` einliest und je Control
  ausgibt: signierter Δ (Bias?) vs. Streuung (Rauschen?), Korrelation mit Zellgröße, schlechteste Zellen.
  → trennt automatisch Rausch von Bias (Leitprinzip 1).
- **M4.2:** Schwellen-gesteuerte Kandidatenliste: nur Controls mit `mean|Δ| > X` UND systematischem Bias
  werden zur Kalibrierung vorgeschlagen.
- **M4.3:** Jede Kalibrierung wird wie die bestehenden (gravity, education) als **Skript + gepinnte Werte
  im Config** abgelegt (reproduzierbar, kein Hand-Tuning).

---

## 5. Priorisierung & Anti-Overfitting

| Prio | Maßnahme | Aufwand | Impact | Overfitting-Risiko |
|---|---|---|---|---|
| 1 | **M2.1** employment → echter P9-Kreis-Margin | mittel | hoch | gering (reale Marginale) |
| 2 | **M1.1** household_size bei 100 % re-validieren (Rausch vs. Bias) | 0 | klärt das Problem | keins |
| 3 | **M3.1** license_type etc. in Output-CSV | klein | mittel (3 Controls validierbar) | keins |
| 4 | **M4.1** Diagnose-Skript (Rausch/Bias-Trennung) | klein | hoch (Infrastruktur) | senkt es |
| 5 | M1.3 Gemeinde-Größen-Rake | mittel | nur falls Bias bleibt | mittel (vorsichtig raken) |

**Bewusst NICHT tun:** mehr Einkommens-Cross-Tabs (Einkommensseite ist gesättigt); Controls ohne reale
Zielquelle erzwingen; auf 25 %-Rauschen kalibrieren. Der größte Realismus-Hebel bleibt strukturell
(ENTD→MiD-Wege-Donor + Re-Estimation der Mode-Choice-Parameter, B1) — separat vom Control-Fit.

## 6. Vorgeschlagene nächste Schritte
1. **M2.1** umsetzen (employment-P9-Margin) — größter, klarster Gewinn.
2. **M4.1** Diagnose-Skript — damit wir household_size sauber als Rauschen vs. Bias einordnen.
3. Dann erst entscheiden, ob M1.3 (Gemeinde-Rake) nötig ist.
