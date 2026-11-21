### Resource record

- samples: 283 (source: proc), wall clock 2:30:54
- peak process RSS: 77.2 GiB (pid 486131, `~/tools/jdk-25.0.3+9/bin/java -cp ~/popsim100_20261120/cache/matsim.runtime.pt2matsim__63258fbf8a6ba`)
- system memory: peak used 93.8 GiB, minimum available 11.5 GiB, peak swap used 676.8 MiB
- peak processes / threads: 67 / 2821
- CPU: 38225 s accounted as `per_pid`, efficiency 0.07 of the machine
- disk: read 14.6 GiB, written 109.8 GiB
- minimum free space on `../cache`: 344.0 GiB
- minimum free space on `../output`: 344.0 GiB
- minimum free space on `../popsim_work`: 344.0 GiB
- kernel events during the run: 0 OOM kill(s), 0 segfault(s)

| stage | wall | CPU s | efficiency | peak process RSS | procs | threads |
|---|---|---|---|---|---|---|
| (no stage tag) | 0:00 | 0 | unknown | 274.6 MiB | 1 | 82 |
| braunschweig.synthesis.commute_day.home_office_donors_stage__48c3d738a2982054e05ae5942cc5fb8a | 0:00 | 0 | unknown | 3.1 GiB | 1 | 82 |
| braunschweig.data.bbsr.regiostar__6fdfd12307fe21a713de1a709c54e930 | 0:00 | 0 | unknown | 4.5 GiB | 1 | 82 |
| braunschweig.popsim.distance_distributions__76263a1e337f8f47fa56ef7a11039805 | 0:30 | 30 | 0.02 | 6.2 GiB | 1 | 82 |
| braunschweig.data.inkar.household_income__92804bcec93411709a8fb763f0a58488 | 0:00 | 0 | unknown | 8.2 GiB | 1 | 82 |
| braunschweig.popsim.completed_donor__693b5a0a42bac73ddcc522d33254e434 | 14:51 | 890 | 0.02 | 8.2 GiB | 1 | 84 |
| matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f | 0:30 | 0 | 0.00 | 8.2 GiB | 2 | 157 |
| braunschweig.popsim.stage__9589e34f5bf566ae8d73e3241e659d6f | 34:55 | 16926 | 0.13 | 15.5 GiB | 27 | 224 |
| data.gtfs.cleaned__1d43af8cb416b046ce99a23899be94bb | 0:30 | 30 | 0.02 | 15.5 GiB | 1 | 157 |
| data.osm.cleaned__a2200b6946907ac361542af3aa8ee9a0 | 7:01 | 425 | 0.02 | 15.5 GiB | 2 | 232 |
| eqasim_common.data.osm.chunked__3a187d0bdff44a97e3641203c243094f | 0:30 | 1 | 0.00 | 15.5 GiB | 67 | 293 |
| braunschweig.synthesis.locations.home_cell__381029e3848c710762147f5018aa4852 | 6:52 | 431 | 0.02 | 15.5 GiB | 3 | 159 |
| braunschweig.popsim.trips_stage__46e5255f09fb7a19d7ea5a879fdb5cb5 | 11:50 | 712 | 0.02 | 30.9 GiB | 3 | 159 |
| braunschweig.matsim.scenario.supply_gtfs__95dd6322478383df6b92adfae14af15e | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| matsim.scenario.supply.osm__e5a6270e322d2630b0905a0beabc8b01 | 3:30 | 1117 | 0.08 | 30.9 GiB | 4 | 274 |
| eqasim_common.data.osm.locations__3a187d0bdff44a97e3641203c243094f | 4:04 | 249 | 0.02 | 30.9 GiB | 3 | 162 |
| braunschweig.popsim.commute_distance__46e5255f09fb7a19d7ea5a879fdb5cb5 | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.vehicles.cars.household__db8e588ba60e615c7a7e967b862ca31d | 4:41 | 287 | 0.02 | 30.9 GiB | 3 | 159 |
| matsim.scenario.supply.processed__4dce13c683a6cc2017996fd709fa2c6e | 4:31 | 9212 | 0.53 | 77.2 GiB | 4 | 775 |
| synthesis.vehicles.vehicles__931147e1f5d5d0b38f29588e6d35b342 | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.data.cordon_network__4dce13c683a6cc2017996fd709fa2c6e | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.data.transit.cut_schedule__9ab330af02439997a971633159338d13 | 0:00 | 0 | unknown | 30.9 GiB | 4 | 237 |
| synthesis.population.spatial.primary.candidates__1395670415a56599aee21872003c3fb3 | 1:30 | 20 | 0.00 | 30.9 GiB | 33 | 2570 |
| braunschweig.synthesis.cordon_gates__11eceeb40f1c607cc7669833cb38e93c | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.locations.education_gravity__20e39344392a23d35a3228be7df6da32 | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.locations.synthesis.replacement_education_gravity__b36746de8808de540bca36eee6c621e2 | 6:03 | 525 | 0.02 | 30.9 GiB | 35 | 2821 |
| braunschweig.analysis.synthesis.commute_distance_by_kreis__d9a03f14d1126856fe63b9ead7263d9b | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.commute_day.trips_day_stage__6a49e835d1a2b7551a8224092075bb75 | 1:00 | 60 | 0.02 | 30.9 GiB | 3 | 159 |
| braunschweig.analysis.synthesis.plan_structure_vs_srv__e0226847513b5c63769af675352c7a5f | 1:02 | 62 | 0.02 | 30.9 GiB | 3 | 159 |
| braunschweig.analysis.synthesis.departure_time_vs_srv__b5678087db4a2febf0daa26465736008 | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.analysis.synthesis.work_participation_by_kreis__585197d01fe02fc32e8df4ec47097336 | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.portal_trips.stage__5f2f0f3fe851cb72e7cc2ec5b4740fbc | 5:20 | 192 | 0.01 | 30.9 GiB | 4 | 291 |
| braunschweig.synthesis.portal_trips.trips_final__5f2f0f3fe851cb72e7cc2ec5b4740fbc | 0:00 | 0 | unknown | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.commute_day.activities_day_stage__5f2f0f3fe851cb72e7cc2ec5b4740fbc | 0:31 | 30 | 0.02 | 30.9 GiB | 3 | 159 |
| braunschweig.synthesis.locations.secondary_chainsolvers__874b64526165a07810e7bcc041f7ff87 | 10:28 | 4576 | 0.11 | 30.9 GiB | 19 | 1138 |
| braunschweig.synthesis.commute_day.spatial_locations_day__874b64526165a07810e7bcc041f7ff87 | 0:30 | 30 | 0.02 | 30.9 GiB | 3 | 96 |
| braunschweig.synthesis.outside_tour_modes.stage__e9c5a07544819d32d13d53fdde20afd6 | 3:33 | 572 | 0.04 | 30.9 GiB | 4 | 230 |
| braunschweig.synthesis.outside_tour_modes.activities_stage__e9c5a07544819d32d13d53fdde20afd6 | 0:33 | 30 | 0.01 | 30.9 GiB | 3 | 96 |
| braunschweig.synthesis.commute_day.output_day__528489bf1fe8e7959f0a3d7bbcd8fc0f | 6:12 | 401 | 0.02 | 30.9 GiB | 3 | 96 |

Worker pools (private memory = what the machine lost beyond the driver's own RSS since the sample before the pool, per live worker; a forked worker's RSS counts the pages it shares with the driver):
- `braunschweig.popsim.stage__9589e34f5bf566ae8d73e3241e659d6f` 2026-11-20T14:26:49 -> 2026-11-20T14:50:53, up to 13 workers: peak 2.07 GiB private per worker (13 workers, 2026-11-20T14:29:20; pool total peak 26.9 GiB; driver RSS 3.0 GiB); from the first pool sample to that peak the private memory per worker grew 1.13 GiB while the mean worker RSS grew -0.00 GiB
- `braunschweig.synthesis.locations.secondary_chainsolvers__874b64526165a07810e7bcc041f7ff87` 2026-11-20T16:12:39 -> 2026-11-20T16:16:40, up to 16 workers: peak 0.44 GiB private per worker (16 workers, 2026-11-20T16:16:10; pool total peak 7.0 GiB; driver RSS 21.4 GiB); from the first pool sample to that peak the private memory per worker grew 0.06 GiB while the mean worker RSS grew 0.01 GiB
- `braunschweig.synthesis.locations.secondary_chainsolvers__874b64526165a07810e7bcc041f7ff87` 2026-11-20T16:17:42 -> 2026-11-20T16:18:12, up to 16 workers: peak 0.40 GiB private per worker (16 workers, 2026-11-20T16:18:12; pool total peak 6.4 GiB; driver RSS 17.3 GiB); from the first pool sample to that peak the private memory per worker grew 0.04 GiB while the mean worker RSS grew 0.00 GiB

No span without a progress signal was recorded.
