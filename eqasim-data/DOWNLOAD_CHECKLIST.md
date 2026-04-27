# Bavaria Pipeline — Download-Checkliste

Alle Dateien exakt unter den angegebenen Pfaden ablegen.
Fortschritt pruefen: `python scripts/verify_bavaria_inputs.py [--matsim]`

## Pflicht (synthesis.output)

- [ ] **Admin-Grenzen Deutschland (VG250-EW)**
  - Ziel: `eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip`
  - Quelle: <https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html>
  - Hinweis: UTM32s / Geopackage / ebenen, Version 12-31

- [ ] **Bevoelkerung Bayern (A1310C)**
  - Ziel: `eqasim-data/data/bavaria/a1310c_202200.xla`
  - Quelle: <https://www.statistik.bayern.de/statistik/gebiet_bevoelkerung/bevoelkerungsstand/>
  - Hinweis: Statistik Bayern, Jahrgang 202200 (Excel 97-2003, .xla)

- [ ] **Erwerbstaetige Bezirk (13111-004r)**
  - Ziel: `eqasim-data/data/bavaria/13111-004r.xlsx`
  - Quelle: <https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=13111>
  - Hinweis: ERW032 *Wohnort* -> Werteabruf -> XLSX Export

- [ ] **Erwerbstaetige Gemeinden (A6502C)**
  - Ziel: `eqasim-data/data/bavaria/a6502c_202200.xla`
  - Quelle: <https://www.statistik.bayern.de/statistik/gebiet_bevoelkerung/erwerbstaetigkeit/index.html>
  - Hinweis: Statistik Bayern, Jahrgang 202200

- [ ] **Haushaltsgroesse (12211-105)**
  - Ziel: `eqasim-data/data/bavaria/12211-105.xlsx`
  - Quelle: <https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=12211>
  - Hinweis: Sex x Age x Haushaltsgroesse

- [ ] **Haushaltseinkommen (12211-101)**
  - Ziel: `eqasim-data/data/bavaria/12211-101.xlsx`
  - Quelle: <https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=12211>
  - Hinweis: Haushaltsgroesse x Einkommensklasse

- [ ] **Fuehrerscheinbestand KBA (FE4)**
  - Ziel: `eqasim-data/data/germany/fe4_2024.xlsx`
  - Quelle: <https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html>
  - Hinweis: Jahrgang 2024 — Sheets FE4.2 / FE4.3 / FE4.4

- [ ] **Hausumringe Bayern — Oberbayern**
  - Ziel: `eqasim-data/data/bavaria/buildings/091_Oberbayern_Hausumringe.zip`
  - Quelle: <https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=hausumringe>

- [ ] **Hausumringe Bayern — Niederbayern**
  - Ziel: `eqasim-data/data/bavaria/buildings/092_Niederbayern_Hausumringe.zip`
  - Quelle: <https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=hausumringe>

- [ ] **Hausumringe Bayern — Schwaben**
  - Ziel: `eqasim-data/data/bavaria/buildings/097_Schwaben_Hausumringe.zip`
  - Quelle: <https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=hausumringe>

- [ ] **ENTD 2008 — Q_individu.csv**
  - Ziel: `eqasim-data/data/entd_2008/Q_individu.csv`
  - Quelle: <https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008>
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2565/download?inline>

- [ ] **ENTD 2008 — Q_tcm_individu.csv**
  - Ziel: `eqasim-data/data/entd_2008/Q_tcm_individu.csv`
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2555/download?inline>

- [ ] **ENTD 2008 — Q_menage.csv**
  - Ziel: `eqasim-data/data/entd_2008/Q_menage.csv`
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2556/download?inline>

- [ ] **ENTD 2008 — Q_tcm_menage_0.csv**
  - Ziel: `eqasim-data/data/entd_2008/Q_tcm_menage_0.csv`
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2339/download?inline>

- [ ] **ENTD 2008 — K_deploc.csv**
  - Ziel: `eqasim-data/data/entd_2008/K_deploc.csv`
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2568/download?inline>

- [ ] **ENTD 2008 — Q_ind_lieu_teg.csv**
  - Ziel: `eqasim-data/data/entd_2008/Q_ind_lieu_teg.csv`
  - Direct: <https://www.statistiques.developpement-durable.gouv.fr/media/2566/download?inline>

## Optional (nur fuer matsim.output)

- [ ] **OSM Bayern (PBF)**
  - Ziel: `eqasim-data/data/osm/bayern-latest.osm.pbf`
  - Quelle: <https://download.geofabrik.de/europe/germany/bayern.html>
  - Direct: <https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf> (~2 GB)

- [ ] **GTFS Deutschland**
  - Ziel: `eqasim-data/data/gtfs/<beliebig>.zip`
  - Quelle: <https://gtfs.de/de/feeds/de_full/>

- [ ] **MVG Stations (JSON)**
  - Ziel: `eqasim-data/data/mvg/stations.json`
  - Quelle: <https://www.mvg.de/.rest/zdm/stations>
