# Poster / presentation figures

Decorative, presentation-grade maps of the eqasim-bs Braunschweig model, produced for
the TRANSFORMPATHS poster (mobility-systems column). These are **visual assets**, not
quantitative analysis: most draw a sample of the demand for legibility and are labelled
accordingly. All read committed run outputs and need no network access.

## Scripts

| Script | Produces |
|--------|----------|
| `region_glow_maps.py`      | Dark/light glow maps: population point cloud + PT desire lines (4 style/extent variants + contact sheet). |
| `editorial_triptych_pop.py`| Wide editorial triptych: population heatmap / PT desire lines / mode comparison. |
| `oepnv_triptych.py`        | OEV-focused triptych (PT demand, hotspots, by time/purpose); white + dark. |
| `link_load_map.py`         | MIV (car) network link-load map from MATSim linkStats (full-bleed, BS-centred). |
| `link_load_fancy.py`       | Fancy link-load variants (full mesh + load-scaled arteries; white & dark glow). |
| `sim_snapshot.py`          | "Simulation snapshot" look: grey network + stops + agent dots coloured by congestion. |
| `white_map_gallery.py`     | ~23 white, label-free map variants (road load, hierarchy, congestion, rail, demand, ...). |
| `pt_analysis_gallery.py`   | 10 PT analyses: stop boardings, access surface, line frequency, travel time/speed/detour, mode share, corridors, AM/PM, demand surface. |

Run with the project Python (conda `eqasim` env, or the repo `.venv`):

```
PYTHONUTF8=1 python braunschweig/analysis/poster/link_load_map.py
```

Outputs are written to `poster_maps/` next to the scripts and are **git-ignored**
(see `.gitignore`). Fonts: Space Mono (SIL Open Font License) in `fonts/`.

## Data sources (committed run outputs, EPSG:25832)

- Population: `eqasim-data/output_bs_100pct/braunschweig_100pct_homes.gpkg`
- Trips (mode, purpose, times, distances): `.../output_bs_100pct/simulation_output/eqasim_trips.csv`
- Transit schedule (stops, lines, departures): `.../output_bs_100pct/braunschweig_100pct_transit_schedule.xml.gz`
- Network geometry: `.../cache_bs_25pct/.../simulation_output/output_network.xml.gz`
- Car link volumes: `.../cache_bs_25pct/.../ITERS/it.50/50.linkstats.txt.gz` (25% run, scaled)
- Kreis boundaries: `eqasim-data/output_bs_25pct/simwrapper/kreis_socio.geojson`

## Important caveat: public transport is teleported in these runs

In the available MATSim runs, public transport is **routed and modelled** (schedule,
PT trips with departure/travel times, distances) but **not simulated on the network in
the mobsim** (no `TransitDriverStarts` / PT vehicle link events). Therefore:

- Network **link loads are car (MIV) only** — there is no PT link-load equivalent.
- PT is represented via **schedule geometry** (rail lines, stops, departure frequency)
  and via **demand** (PT trip desire lines, origin/destination density, boardings).

A true PT link load would require re-running with PT simulated in the mobsim
(`usingTransitInMobsim`).
