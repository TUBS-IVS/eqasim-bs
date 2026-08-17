# IPF household synthesis: joint age x size margin (#3) + age-aware composition (#3b)


All household-synthesis features below are **flag-gated with a code default of
off**, so the pipeline stays byte-identical to the legacy formation unless a
config enables them. They build on the per-commune household-size margin
(`braunschweig.ipf.use_household_size_margin`, Zensus 2022 1000A-2081).

Since issue #251 all three are **enabled in every committed `simple_ipf_open` run
config** (`config_local_braunschweig.yml`, `..._10pct.yml`, `..._25pct.yml`,
`config_smoke_simple_ipf.yml`), pinned by `tests/test_ipf_config_parity.py`. They
are deliberately **not** set in the `popsim_mid` / `popsim_open` configs: the
production population method replaces the legacy IPF, so the keys would be dead
there. Before #251 they were set in no config at all, so no run had ever executed
them despite the ADRs treating them as decided.

### Joint age x household-size margin (#3)

`braunschweig.ipf.joint_age_size` adds the observed **age x household-size
correlation** to the IPF. The flat size margin balances size independently of
age, so the IPF would otherwise invent the joint (it would not know that large
households skew toward school-age children while 1-person households skew toward
the elderly). The joint is enforced at **Kreis** resolution over coarse age
groups, raked (2D IPF) to be consistent with BOTH the population age-group
marginal and the size marginal already in the IPF -- so adding it **cannot make
the IPF infeasible**. Source: Zensus 2022 **1000A-3082** (persons by
Gemeinde x age x sex x hh_size), loaded by
`braunschweig.data.census.households_size_age`. Enabled by
`braunschweig.ipf.use_joint_age_size_margin` (requires the size margin).

The coarse age groups are `DEFAULT_AGE_GROUP_BOUNDS = (15, 30, 40, 50, 60)` ->
`[0,15) [15,30) [30,40) [40,50) [50,60) [60,inf)`. **All edges are native
1000A-3082 ALTKL2 band edges** (0,5,10,15,20,25,30,40,50,60,75), so aggregating
the Zensus joint never splits a band (no assumption). The middle band is split at
**40 and 50** to give the joint a finer age resolution for family-size households
(real ZGB Zensus data show sizes 4/5/6+ concentrate in `[30,40)`/`[40,50)`, which
the old single `[30,60)` group could not pin). On its own this does **not** reduce
the parent-child age-gap tail -- that tail was dominated by a household-formation
routing bug (surplus children landing on elderly childless-shell adults), fixed
separately by the children-driven composition (see #3b below). Once that fix is in
place the finer bounds **do** reduce the residual tail: on the real ZGB IPF (25 %,
age-aware chunking) the parent-child gap>50 share falls 2.70 % -> 0.77 % and
gap>55 -> 0.03 % with the refined bounds vs the old `[30,60)` group. The bounds are
read from the config key `braunschweig.ipf.joint_age_group_bounds` (default =
`DEFAULT_AGE_GROUP_BOUNDS`), registered in both `braunschweig.ipf.prepare` and
`braunschweig.ipf.model` so a change correctly invalidates the synpp cache. A
**structural zero** (children below
`braunschweig.minimum_age.one_person_household`, default 16, in a 1-person
household) is held at exactly zero in the rake so it agrees with the IPF hard
zero (otherwise the full IPF diverges).

### Age-aware household composition (#3b)

`braunschweig.ipf.household_composition` + `form_households_age_aware`
(in `braunschweig.ipf.attributed`) replace the random within-bucket chunk + the
independent hh_type draw with one coupled, optimisation-based pass per
`(commune_id, hh_size)` bucket. Enabled by
`braunschweig.ipf.age_aware_chunking`. Adult/child composition per `hh_type` is a
HARD constraint; within it: couples are paired minimising the within-pair age gap
(jittered by `couple_age_std`, default 4.0, for a realistic spread), young
couples are routed to child-rearing households, and children are placed by a
**sorted rank match** (the 1-D optimum of the parent-child age-gap deviation
around a per-household target drawn `N(parent_child_gap_years, parent_child_gap_std)`,
defaults **31.8** = Destatis 2024 mean mother age at birth, **5.5**; clipped to
`parent_child_gap_max`, 50). The sorted match replaced a Hungarian
`linear_sum_assignment`: it is the same optimum but `O(n log n)` instead of
`O(n^3)`, which is essential because formation runs on the **full** population
(the attributed stage is upstream of sampling, so even a 25 % output forms
households on all ~1.13 M persons; the dense LAP was a hard wall on large urban
buckets). hh_type counts per bucket are allocated by the largest-remainder method,
but **children drive the composition**: `_ensure_child_capacity` grows the
child-bearing capacity until it covers every child in the bucket (the IPF places
more children in a cell than the Zensus single_parent share provides shells for),
so no surplus child spills onto the oldest childless-shell adults. Without this,
~23 % of placed children had a youngest household adult 55+ years older (mean 84 --
implausible "single parents"); with it the gap>55 tail drops to ~0.3 % (~0.03 %
with the refined bounds) and the mean gap from 39 to 26 years.

The children-driven fix gives child households the youngest adults, which pulls
the realised mean gap *below* the target (26 vs 31.8: 18-25-year-olds become
parents of newborns). `child_parent_age_target_weight` corrects this -- child
households claim a contiguous window of the age-sorted adults centred on
(median child age + gap) rather than the absolute youngest, leaving the very
youngest adults for childless young couples/singles. The weight blends from 0
(youngest, mean 26) to 1 (fully targeted, mean 33); the default **0.85** is
calibrated on the real ZGB IPF to a realised mean of **31.8** (= Destatis
`parent_child_gap_years`), with the gap>55 tail unchanged at ~0.04 %.

No person is ever dropped; **all-children households are hard-blocked**
(in-bucket merge + a global
cross-bucket same-commune merge). Config keys live under `braunschweig.chunking.*`.

**Sex-aware couple pairing.** With `braunschweig.chunking.sex_aware_couples` on,
couples are paired **opposite-sex by default** with a small calibrated same-sex
share `braunschweig.chunking.same_sex_couple_share`
(`DEFAULT_SAME_SEX_COUPLE_SHARE = 0.011`). Provenance: Statistisches Bundesamt,
**Mikrozensus 2025**, Tabelle "Gleichgeschlechtliche Lebensgemeinschaften" --
204 000 same-sex couples (102k male / 102k female, ~50/50) against ~18.9 M
couples => ~1.1 %. Pairing is `pair_adults_sex_aware`, an **opposite-first**
allocation: the number of same-sex couples in a block is
`max(intended, forced)`, where `intended ~ Binomial(k, share)` is the genuine
share and `forced = |#males - #females| / 2` is the minimum imposed by the
block's sex imbalance (so nobody is dropped). Within each group, partners are
paired adjacently in jittered-age order (small within-couple gaps, opposite pairs
rank-aligned). The 50/50 male/female split emerges from the balanced pool.
Default off (`is_female=None`) -> the legacy sex-blind age-adjacent pairing,
byte-identical.

The realised share **converges toward 1.1 % as the sampling rate rises** because
the per-(commune, hh_size) bucket imbalance floor shrinks: on the cached ZGB
population it is ~4.8 % at 5 %, **~2.9 % at 25 %**, and approaches the ~1.1 %
target at 100 % (the residual is the genuine local sex imbalance in small
Gemeinden). For contrast, the sex-blind pairing yields **~48 %** same-sex couples
(every age-adjacent pair is sex-random) -- the reason the feature exists.

Tests: `tests/test_joint_age_size.py`, `tests/test_household_composition.py`,
`tests/test_run_household_composition.py`.
