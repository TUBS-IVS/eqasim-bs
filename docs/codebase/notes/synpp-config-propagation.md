# synpp config scoping: why a helper's options must be declared by every caller

**Rule for maintainers: if a stage's `execute()` reaches a helper's `run()`, that
stage's `configure()` must call the helper's own `configure(context)`.** Never rely
on the option arriving by itself. For options declared `volatile` this is not a
recommendation but a hard requirement (see below).

## The mechanism

`synpp.pipeline.ExecuteContext.config(option)` raises
`PipelineError("Config option %s is not requested")` unless the option is present in
`required_config` — and `required_config` is built by `ConfigurationContext` from
what **that same stage's** `configure()` declared. Helper modules read options from
the CALLING stage's context: `matsim.runtime.java.run` reads `java_binary` /
`java_memory` (plus the hang-watchdog options, ADR-0095),
`matsim.runtime.pt2matsim.run` additionally reads `pt2matsim_version`,
`matsim.runtime.maven.run` reads `java_home`, and so on. So a stage that calls
`eqasim.run` / `pt2matsim.run` / `java.run` reads options it never declared.

That this works at all in many places is due to a second pass in
`synpp.pipeline` ("Update configuration requirements based dependencies"), which
copies an upstream stage's config options into its downstream stage's config
(`passed_config_options`). It is why `matsim.simulation.run` could call
`java.run` for years while declaring neither `java_binary` nor `java_memory` — the
2026-08-20 100 % run demonstrably spawned the JVM with `-Xmx100G` from the global
config.

## Why it cannot be relied on

Two independent reasons:

1. **Volatile options are excluded on purpose.** The propagation loop skips every
   key in the upstream stage's `volatile_config`. An option declared with
   `volatile = True` therefore NEVER reaches a downstream stage implicitly. This is
   why `matsim.simulation.run`, `matsim.simulation.prepare` and
   `braunschweig.freight.extraction` now delegate to
   `matsim.runtime.java.configure(context)` — the hang-watchdog keys are volatile so
   that changing a timeout cannot invalidate a cached stage (ADR-0095).
2. **Propagation does not reach every consumer.** `matsim.scenario.supply.osm`
   declares `context.stage("matsim.runtime.pt2matsim")` and still died with
   `"Config option pt2matsim_version is not requested"` in the 100 % run of
   2026-08-20, the moment the eqasim-java 2.3.0 bump devalidated its long-lived
   cache (recorded in `docs/runs/100pct-allfeat-i240-2026-08-20.yml`; fixed in PR
   #325 by delegating to `pt2matsim.configure`). The traversal enqueues only one
   downstream branch per stage, which is the suspected reason — but the exact
   condition under which a consumer is reached has NOT been characterised here, and
   nothing in this repository should depend on it either way.

## The failure mode this produces

The crash is **delayed**: a stage keeps running from cache for weeks and only fails
once something devalidates it. That is the whole #222 / #223 / #229 family, and it
is the reason the guard is a test rather than a review habit.

## The guard

`tests/test_runtime_config_declares.py` DISCOVERS its subjects by scanning the
first-party trees for `pt2matsim.run(` and `eqasim.run(` call sites and asserts that
each calling module's `configure()` declares the full option set the helper reads
(`PT2MATSIM_RUN_KEYS`, `JAVA_RUN_KEYS`). A new caller is therefore covered on
arrival instead of waiting for the next cache devalidation to expose it, and the
discovery itself is pinned against returning an empty subject list (a vacuous
green). Modules that only DEFINE a wrapper are exempted explicitly, by name, in
that file.
