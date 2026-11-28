# University students: synthetic population vs SrV 2023

A measurement, not validation: the tables below state differences in SrV standard errors; no synthesis stage reads them.

Parameters: output_subdir=analysis/students_vs_srv, sampling_rate=1.0, max_unmatched_home_share=0.05, day_absence_enabled=True, portal_layer_enabled=True, trips_stage=braunschweig.synthesis.commute_day.trips_day_stage
Generated at: 2026-11-27T16:41:37.133355+00:00
Pipeline commit: 760bff86
SrV tables: srv2023_student_parameters.csv, srv2023_student_share_by_kreis.csv, srv2023_young_adult_status_by_kreis.csv, srv2023_student_parameters_by_area.csv, srv2023_education_leg_by_student_status.csv

## Universe and scope

Region row 03ZGB = the 7 SrV-surveyed Kreise ['03101', '03102', '03151', '03153', '03154', '03157', '03158'] (ZGB_KREISE minus Wolfsburg 03103) on the model side; the SrV region row is built over the ZGB Kreise, of which SrV surveys no person in Wolfsburg. Wolfsburg rows are model-only with NaN SrV columns.

Adult students: 25143 present and 2070 away all day (general day-absence draw); 0 without a home Kreis. Model counts are sample counts at sampling_rate=1.0, unweighted.
Shares are over PRESENT persons (the SrV universe: persons at home at some point of the reporting day); away_all_day is over all students.

An SrV standard error of 0 (a share of 0 or 1) gives a NaN difference_in_se and an NA beyond_two_se: no finite distance in standard errors exists there.

## Files

* student_behaviour_vs_srv.csv: 66 parameter cells; 64 with a difference in SrV standard errors, of which 21 beyond two standard errors; 0 cell(s) missing from the SrV table.
* student_level_by_kreis.csv: region 03ZGB student share of the present adults model 0.0284 vs SrV 0.0424 (difference -5.57 SrV standard errors).
  srv_students_weighted_persons counts only the SrV students at home on the reporting day (the participation universe), so its like-for-like model count is model_present_students_adults, not the count of all model students; the SrV students away all day are added back in Phase 2 (design A11).
* young_adult_status_by_kreis.csv: student / employed / other among the 20 to 29 year olds, model against SrV, with the Zensus 2022 employed count on the employed rows.
* student_work_class.csv: the adult students by employed flag and employment_status class of the work control.
* student_plan_source.csv: own diary or re-drawn plan, match path and donor activity; share_of_students over all adult students, the leg shares (share_with_education_leg, share_with_work_leg) over the present students of the row. Trace files available: True.
* student_behaviour_by_area.csv: the parameter cells once per home area (braunschweig, surrounding against their SrV rows, wolfsburg model only with NaN SrV columns); 0 cell(s) of the surveyed areas missing from the SrV table.
* education_leg_by_student_status.csv: share of the present adults with an education leg, students and other adults, per Kreis and (region) age range; region 18plus students model 0.3812 vs SrV 0.3706 (difference 0.31 SrV standard errors), other adults model 0.0356 vs SrV 0.0333 (difference 0.99).

