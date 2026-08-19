<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Data registry (generated)

One row per dataset record in `docs/registry/data/`. `Required for` uses
S=synthesis, M=matsim, P=production (r=required, o=optional, -=not needed).
Restricted datasets are never committed or redistributed.

| Dataset | Roles | Acquisition | Destination (eqasim-data/...) | Required S/M/P | Restricted |
|---|---|---|---|---|---|
| [alkis_buildings](../registry/data/alkis_buildings.yml) | spatial_input | manual_download (`scripts/preprocess_alkis_landuse.py`) | `data/braunschweig/buildings/gebaeude-ni.zip -> braunschweig/preprocessed/alkis_buildings.parquet` | r/-/r | no |
| [atkis_landuse](../registry/data/atkis_landuse.yml) | spatial_input | manual_download (`scripts/preprocess_alkis_landuse.py`) | `data/braunschweig/landuse/FS_LN_03_NI_260101.zip -> braunschweig/preprocessed/landuse.parquet` | r/-/r | no |
| [ba_gemband](../registry/data/ba_gemband.yml) | control, reference_table | manual_download | `data/braunschweig/gemband-dlk-0-202506-xlsx.xlsx` | o/-/o | no |
| [ba_pendler_detailed](../registry/data/ba_pendler_detailed.yml) | calibration_target | auto_script (`scripts/download_ba_pendler_detailed.py`) | `data/braunschweig/ (per-export file name)` | -/-/- | no |
| [ba_pendleratlas](../registry/data/ba_pendleratlas.yml) | calibration_target, control | manual_download | `data/braunschweig/statistik_pendler_2026042493412.csv (+ ...93430.csv)` | r/-/r | no |
| [blended_kreis_targets](../registry/data/blended_kreis_targets.yml) | control, calibration_target | derived | `data/braunschweig/targets/target2026_*.csv` | o/-/o | no |
| [bosserhof_mappings](../registry/data/bosserhof_mappings.yml) | reference_table | committed | `data/braunschweig/buildings/bosserhof_class_to_{purpose,location_category}.csv` | o/-/r | no |
| [building_activity_potentials](../registry/data/building_activity_potentials.yml) | derived_input | derived (`scripts/import_building_activity_potentials.py`) | `data/braunschweig/buildings/building_activity_potentials.parquet` | o/-/r | no |
| [buildings_with_households](../registry/data/buildings_with_households.yml) | derived_input | derived | `data/braunschweig/popsim/buildings/buildings_with_households_zgb.parquet` | o/-/r | no |
| [clc_backbone](../registry/data/clc_backbone.yml) | spatial_input | manual_download | `data/ (user-provided)` | o/-/o | no |
| [cleancensus_kreis_controls](../registry/data/cleancensus_kreis_controls.yml) | control | derived | `data/braunschweig/popsim/kreis_controls/` | o/-/r | no |
| [destatis_population_kreis](../registry/data/destatis_population_kreis.yml) | control | manual_download | `data/braunschweig/12411-0018_de.csv` | r/-/o | no |
| [detour_circuity_params](../registry/data/detour_circuity_params.yml) | calibration_target | committed | `data/braunschweig/calibration/detour_circuity_params.csv` | o/-/o | no |
| [entd_2008](../registry/data/entd_2008.yml) | donor | manual_download | `data/entd_2008/{Q_individu,Q_tcm_individu,Q_menage,Q_tcm_menage_0,K_deploc,Q_ind_lieu_teg}.csv` | r/-/r | no |
| [genesis_svb_residence](../registry/data/genesis_svb_residence.yml) | control | manual_download | `data/braunschweig/13111-06-02-4.xlsx` | r/-/o | no |
| [genesis_svb_workplace](../registry/data/genesis_svb_workplace.yml) | control | manual_download | `data/braunschweig/13111-01-03-5.xlsx` | r/-/r | no |
| [german_wide_freight_v3](../registry/data/german_wide_freight_v3.yml) | supply_input, network, assumption_basis | auto_script (`scripts/download_german_wide_freight.py`) | `data/braunschweig/freight/german-wide-freight-v3/` | -/r/r | no |
| [gtfs_feed](../registry/data/gtfs_feed.yml) | supply_input | manual_download | `data/gtfs_cordon/de_full_2026-06-02.zip` | -/r/r | no |
| [hsn_tsn_lookup](../registry/data/hsn_tsn_lookup.yml) | derived_input | scrape (`scripts/scrape_hsn_tsn.py`) | `data/braunschweig/kba/hsn_tsn_lookup.csv` | o/-/r | no |
| [inkar_household_income](../registry/data/inkar_household_income.yml) | control, reference_table | manual_download | `data/braunschweig/E_Haushaltseinkommen.xls` | r/-/r | no |
| [inkar_indicators](../registry/data/inkar_indicators.yml) | reference_table | manual_download | `data/braunschweig/E_*.xls` | o/-/o | no |
| [kba_fe4_licences](../registry/data/kba_fe4_licences.yml) | reference_table | manual_download | `data/germany/fe4_2024.xlsx` | o/-/- | no |
| [kba_fleet_derived](../registry/data/kba_fleet_derived.yml) | reference_table, calibration_target | committed (`scripts/extract_kba_fleet.py`) | `data/braunschweig/kba/derived/*.csv` | o/-/r | no |
| [kba_fz_registrations](../registry/data/kba_fz_registrations.yml) | reference_table | manual_download (`scripts/extract_kba_fleet.py`) | `data/braunschweig/kba/{fz27_202501.xlsx,fz12_2025.xlsx,raw/}` | o/-/o | no |
| [lod2_heights](../registry/data/lod2_heights.yml) | spatial_input | manual_download (`scripts/preprocess_lod2_heights.py`) | `data/braunschweig/ (preprocessed into the buildings parquet)` | o/-/r | no |
| [lsn_bbs_share_by_age](../registry/data/lsn_bbs_share_by_age.yml) | reference_table | committed | `data/braunschweig/nds_bbs_share_by_age.csv` | o/-/o | no |
| [lsn_income_tax](../registry/data/lsn_income_tax.yml) | reference_table | auto_script (`scripts/extract_lsn_income_tax_kreis.py`) | `data/braunschweig/lsn/lsn2022_income_tax_by_kreis.csv` | -/-/- | no |
| [lsn_kitas](../registry/data/lsn_kitas.yml) | supply_input | manual_download (`scripts/extract_nds_kitas.py`) | `data/braunschweig/schools/nds_kitas_zgb.csv` | o/-/r | no |
| [lsn_schools](../registry/data/lsn_schools.yml) | supply_input | manual_download (`scripts/extract_nds_schools.py`) | `data/braunschweig/schools/nds_schools_zgb.csv` | o/-/r | no |
| [lsn_universities](../registry/data/lsn_universities.yml) | supply_input | manual_download (`scripts/seed_nds_hochschulen.py`) | `data/braunschweig/schools/nds_hochschulen.csv` | o/-/r | no |
| [mid2023_b1](../registry/data/mid2023_b1.yml) | donor, reference_table | restricted_delivery | `The B1 package lives OUTSIDE this repository, in the sibling popsimprep checkout:
  <popsimprep>/inputs/MiD2023/MiD2023_B1_Datensatzpaket/CSV/MiD2023_{Haushalte,Personen,Wege,
  Autos,Etappen,Reisen,Tagesreisen}.csv
That is the default path of scripts/build_mid_age_by_segment_status.py and
scripts/build_mid_antrieb_by_status.py (override with --mid-path). The three files the popsim
donor path needs are additionally synced to
data/braunschweig/popsim/mid2023_raw/MiD2023_{Haushalte,Personen,Wege}.csv on the run
host; the Autos file is NOT synced there.
The CODEBOOK package (MiD2023_Codeplaene_B1_Standard_v1.1.xlsx, MiD2023_HandbuchZurDatennutzung.pdf)
is synced to data/braunschweig/popsim/mid2023_raw/codebook/ from
X:/ivs/14_Daten/MiD/MiD2023/MiD2023_B1_Codebook_HandbuchDatennutzung. Local-only like the
microdata; it is the authority for variable value labels and settled the W_ZWECK 13-16 labels
for issue #241 (sheet "Wege"), which had previously been inferred from MiD's own derived
variables.
` | o/-/r | YES |
| [mid2023_bikes_by_rs7_haustyp](../registry/data/mid2023_bikes_by_rs7_haustyp.yml) | control, assumption_basis | committed (`scripts/extract_mid_ownership_by_rs7_haustyp.py`) | `data/braunschweig/mid/mid2023_bikes_by_rs7_haustyp.csv` | o/-/r | no |
| [mid2023_cars_by_rs7_haustyp](../registry/data/mid2023_cars_by_rs7_haustyp.yml) | control, assumption_basis | committed (`scripts/extract_mid_ownership_by_rs7_haustyp.py`) | `data/braunschweig/mid/mid2023_cars_by_rs7_haustyp.csv` | o/-/r | no |
| [mid2023_mit_tables](../registry/data/mid2023_mit_tables.yml) | reference_table | manual_download (`scripts/extract_mid_income_by_size.py`) | `data/braunschweig/mid/ (mid2023_income_by_*.csv sources)` | o/-/o | no |
| [mid2023_reference_tables](../registry/data/mid2023_reference_tables.yml) | reference_table, validation_reference, calibration_target | committed | `data/braunschweig/mid/mid2023_*.csv` | r/-/r | no |
| [mid2023_regional_report](../registry/data/mid2023_regional_report.yml) | validation_reference, calibration_target | restricted_delivery (`scripts/extract_mid_tables.py`) | `data/braunschweig/mid/ (source PDFs local-only)` | o/-/o | YES |
| [mikrozensus_pendler_modes](../registry/data/mikrozensus_pendler_modes.yml) | validation_reference, calibration_target | auto_script (`scripts/download_mikrozensus_pendler.py`) | `data/braunschweig/mikrozensus/mikrozensus2024_*.csv` | o/-/r | no |
| [mikrozensus_school_distance](../registry/data/mikrozensus_school_distance.yml) | calibration_target | auto_script (`scripts/seed_mikrozensus_school_distance.py`) | `data/braunschweig/mikrozensus/mikrozensus2024_school*.csv` | o/-/o | no |
| [osm_cordon_ring](../registry/data/osm_cordon_ring.yml) | network | derived (`scripts/clip_osm_to_cordon_ring.py`) | `data/osm/germany-latest.zgb_ring.osm.pbf (+ osm/cordon/)` | -/r/r | no |
| [osm_niedersachsen](../registry/data/osm_niedersachsen.yml) | spatial_input, network | manual_download (`scripts/preprocess_osm_pois.py`) | `data/osm/niedersachsen-latest.osm.pbf -> braunschweig/preprocessed/osm_pois.parquet` | r/r/r | no |
| [regiostar](../registry/data/regiostar.yml) | spatial_input, reference_table | auto_script (`scripts/download_regiostar.py`) | `data/regiostar/regiostar_referenzdatei.xlsx` | r/-/r | no |
| [rvb_visum_taz](../registry/data/rvb_visum_taz.yml) | spatial_input | restricted_delivery (`scripts/import_rvb_verkehrszellen.py`) | `data/braunschweig/taz/rvb_verkehrszellen_epsg25832.parquet` | -/-/- | YES |
| [srv2023_raw](../registry/data/srv2023_raw.yml) | calibration_target, validation_reference | restricted_delivery (`scripts/derive_srv_location_types.py`) | `data/braunschweig/srv/ (raw SUF local-only)` | -/-/o | YES |
| [srv2023_reference_tables](../registry/data/srv2023_reference_tables.yml) | control, calibration_target, validation_reference | committed | `data/braunschweig/srv/srv2023_*.csv` | o/-/r | no |
| [urbistat_gemeinde_age](../registry/data/urbistat_gemeinde_age.yml) | derived_input | scrape (`scripts/scrape_urbistat_bs.py`) | `data/braunschweig/urbistat_age_gemeinden.csv` | o/-/- | no |
| [verbindungen](../registry/data/verbindungen.yml) | validation_reference, calibration_target, spatial_input | auto_script (`scripts/download_verbindungen.py`) | `data/verbindungen/` | r/-/r | no |
| [vg250_ew](../registry/data/vg250_ew.yml) | spatial_input | manual_download | `data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip` | r/r/r | no |
| [vrb_tariff_zones](../registry/data/vrb_tariff_zones.yml) | supply_input | scrape (`scripts/build_vrb_stations_json.py`) | `data/vrb/tarifzonen.html -> vrb/stations.json` | -/r/r | no |
| [zensus2022_age_sex_size](../registry/data/zensus2022_age_sex_size.yml) | control | manual_download | `data/braunschweig/1000A-3082_de_flat.zip` | o/-/- | no |
| [zensus2022_employment_age](../registry/data/zensus2022_employment_age.yml) | control | manual_download | `data/braunschweig/popsim/zensus2022_employment_by_age_ref.csv` | o/-/r | no |
| [zensus2022_grid_cells](../registry/data/zensus2022_grid_cells.yml) | control | derived | `data/braunschweig/popsim/cells/zensus2022_grid_{100m_de_prepared,1km_de_binned}.parquet` | r/-/r | no |
| [zensus2022_grid_open](../registry/data/zensus2022_grid_open.yml) | spatial_input | auto_script (`scripts/download_zensus_grid.py`) | `data/zensus_grid/population_100m.parquet` | r/-/r | no |
| [zensus2022_households_size](../registry/data/zensus2022_households_size.yml) | control | manual_download | `data/braunschweig/5000H-2001_de_flat.csv` | r/-/o | no |
| [zensus2022_households_type](../registry/data/zensus2022_households_type.yml) | control | manual_download | `data/braunschweig/1000A-2081_de_flat.zip` | o/-/- | no |
