# ADR-0148 · 2026-10-21 · The freight extraction runs from its own MATSim contrib jar, and the eqasim jar is keyed on source content

- **Status:** accepted (maintainer request, 2026-10-16, issue #468)
- **Design:** `docs/superpowers/specs/2026-10-16-content-addressed-stage-cache-design.md`, section 2
  finding 1 and section 4.8.
- **Numbering:** ADR-0148 is the next free id. Checked on 2026-10-21 across all local and
  remote branches: the highest record anywhere is ADR-0147.
- **Issue:** #468. **Relates to:** ADR-0030 (freight design), ADR-0038 (own eqasim-java-bs
  fork), ADR-0098 (the Java pom owns the jar version).

## Context

- `matsim.runtime.eqasim.validate()` keyed the built jar on the newest modification time
  (mtime) of any `.java`, `.xml` or `.properties` file under `eqasim_source_path`. It walked
  the directory (`os.walk`), excluded only `target` and `.git`, and so also counted the agent
  worktrees under `.claude/worktrees` inside the Java repository. A checkout, a copy or an
  edit anywhere in the fork therefore rebuilt the jar.
- `braunschweig.freight.extraction` depended on that stage and ran its Java entry point
  through it, although `RunExtractFreightTrips` is a four-line passthrough to MATSim's stock
  `org.matsim.application.prepare.longDistanceFreightGER.tripExtraction.ExtractRelevantFreightTrips`.
  The routing result depends on the MATSim version, the compiler level, the inputs and that
  one class, not on the eqasim code.
- Consequence in a real run (run manifest `docs/runs/freight-boundary-links-ab-1pct-2026-10-16.yml`,
  ON arm): a new Java source mtime devalidated `matsim.runtime.eqasim`, and with it
  `braunschweig.freight.extraction`, which ran 05:33 to 06:48 (1 h 15 min), then
  `freight.trips`, `braunschweig.matsim.simulation.prepare` and the rest. The 1 h 15 min is the
  observed duration of that stage in the ON arm of the manifest (1 % scenario, `sampling_rate`
  0.01, ZGB-8 region, cordon and freight on, run on the server; the extraction itself does not
  depend on the sampling rate). The estimate of about 45 min per trip category, four categories
  in all, is the one in `docs/features/freight.md`; it is not an observation of that run.
- Size of the mtime scan, measured on 2026-10-21 against the main checkout of the fork
  (`C:/Users/bienzeisler/Documents/GitHub/eqasim-java-bs`, read-only): the old rule (`os.walk`,
  skipping paths containing `target` or `.git`, suffix `.java`/`.xml`/`.properties`) matched
  5973 files, of which 5328 lie under `.claude`. The file list of the new rule (below) has 640
  entries. The old count moves with the number of worktrees present.

## Decision

1. **A tool stage for the freight Java code.** New stage `braunschweig.freight.extraction_tool`
   (`braunschweig/freight/extraction_tool.py`) renders a minimal Maven project into its cache
   directory: a pom with the dependency `org.matsim.contrib:application` and the shade plugin
   (main class `org.eqasim.braunschweig.freight.RunExtractFreightTrips`), plus the wrapper
   `braunschweig/freight/java/RunExtractFreightTrips.java`. It builds a shaded jar of
   `freight-tool/target/freight-tool-<matsim version>.jar` and returns that relative path.
2. **Version lockstep through the fork pom.** `_versions` reads `matsim.version` and
   `maven.compiler.source` from the fork's root `pom.xml` (via `eqasim_source_path`), so the
   tool and the Java fork cannot drift apart. `read_pom_property` raises when a property is
   missing; an empty `eqasim_source_path` raises as well. The config key
   `freight_tool_matsim_version` (default empty) overrides the MATSim version when a different
   release must be pinned on purpose.
3. **Content token of the tool stage.** `validate()` returns
   `{"format": 1, "matsim_version", "java_release", "wrapper", "pom"}`, where `wrapper` is the
   sha256 of the wrapper source (read in text mode, so LF and CRLF checkouts of the wrapper
   give the same token) and `pom` is the sha256 of the rendered pom. The token carries version
   strings and content only. `eqasim_source_path` is a locator, not an input: it is declared
   volatile in `braunschweig.freight.extraction_tool.configure`, so moving the Java checkout
   does not rename the cache entry, while the MATSim version and Java release read from its
   pom still enter the token.
4. **The dependency switch.** `braunschweig.freight.extraction.configure` depends on
   `braunschweig.freight.extraction_tool` instead of `matsim.runtime.eqasim`. `execute` calls
   `java.run(context, entry_point, args, class_path=<tool jar>)` instead of `eqasim.run(...)`,
   with the entry point and absolute class path built by `tool_java_invocation`. The arguments
   of the four category runs are unchanged.
5. **Content token for the eqasim jar.** `matsim.runtime.eqasim.validate()` returns
   `{"format": 1, "source": <digest>}` for `eqasim_source_path`, and `{"format": 1, "jar": <sha256>}`
   for a prebuilt `eqasim_path` jar. `source_tree_digest` hashes, sorted by path, each file's
   relative path (with `/` separators) and the sha256 of its content with CRLF normalised to
   LF (the jar token hashes raw bytes), for the files reported
   by `git ls-files -z --cached --others --exclude-standard` with a suffix in `SOURCE_SUFFIXES`
   (`.java`, `.xml`, `.properties`), excluding any file below a directory named `target` or
   `.claude` (`SOURCE_EXCLUDED_DIRECTORIES`) at any depth and any file named
   `dependency-reduced-pom.xml` (`SOURCE_EXCLUDED_FILE_NAMES`; the fork tracks it and the shade
   plugin rewrites it on every local build). Tracked files that are deleted in the
   work tree are skipped. The digest does not read mtimes.
   When both keys are set, `eqasim_path` wins, as in `execute()`, so the token describes the jar
   that is provided.
6. **No fallback.** A source path that is not a git work tree, or a missing `git` (the binary
   is the pipeline's `git_binary` option), raises `RuntimeError`. So do a source path that is not
   the root of its own work tree (a subdirectory of another repository, possibly gitignored by
   it, would be listed against the wrong repository), a file name that is not valid UTF-8, and a
   listing with no source file left after the filters (the token would be constant). A directory
   walk or an mtime key would silently apply a different cache rule.
7. **Copy step.** `execute()` of `matsim.runtime.eqasim` now also ignores `.claude` in
   `shutil.copytree`, so agent worktrees are neither copied into the build directory nor part
   of the token.

## Rejected alternatives

- **Hash only the freight-relevant classes of the fork jar.** It would need a Java dependency
  walk (which classes the freight extraction really loads) maintained next to the fork. The
  tool jar removes the question: the extraction does not load fork code at all.
- **Keep the dependency on `matsim.runtime.eqasim` and rely on early cutoff.** An edit to, say,
  the mode-choice model still changes the bytes of the fork jar, so a jar-content token would
  still devalidate the freight stage. Early cutoff only helps when the parent output is
  unchanged, which is not the case here.
- **Pin a hard-coded MATSim version in the tool pom.** It would drift from the fork's
  `matsim.version` at the next MATSim upgrade; reading the fork pom keeps both on one version.
- **Fall back to an mtime or directory-walk key when git is unavailable.** Rejected under the
  no-silent-fallback rule (see decision 6): a different key rule is a different cache.

## Consequences

- **One-time freight recompute.** `braunschweig.freight.extraction` gets a new dependency set
  (the tool stage instead of `matsim.runtime.eqasim`), so it is recomputed once on every
  existing cache. Afterwards a Java edit in the fork no longer reaches it or the stages that
  depend on it only through it. The fork jar is no longer an input of `braunschweig.freight.*`.
- **One-time jar rebuild.** The token format of `matsim.runtime.eqasim` changed (a number
  became a structured value), so the jar is rebuilt once on every existing cache, and with it
  every stage that depends on the jar, once.
- **Maven downloads the contrib once per machine and user.** The new stage makes Maven resolve
  `org.matsim.contrib:application` and its dependencies from `repo.matsim.org` and the OSGeo
  repository into the local Maven repository (`~/.m2` unless the Maven settings say otherwise)
  and shade them. `run` in `matsim/runtime/maven.py` sets no `maven.repo.local`, so the cache
  directory does not hold the downloads. The first build on a machine or user without them needs
  network access.
- **Line-ending-normalised digest.** `source_tree_digest` in `matsim/runtime/eqasim.py` hashes
  each source file (`.java`, `.xml`, `.properties`, all text) after normalising CRLF to LF, so a
  Windows `core.autocrlf` checkout and an LF checkout of the same commit give the same digest,
  independent of mtimes. The prebuilt-jar token (`eqasim_path`) hashes the raw bytes of the
  binary jar. The wrapper hash of the tool stage is read in text mode and is likewise
  line-ending independent.
- **Freight now needs `eqasim_source_path`.** The tool reads `matsim.version` and the Java
  release from the fork pom, so a setup with only a prebuilt `eqasim_path` jar fails loudly at
  `braunschweig.freight.extraction_tool`. `freight_tool_matsim_version` alone does not lift
  this, because `java_release` still comes from the pom.
- **Limits of the source digest.** Resources with other suffixes (for example an `.html` file
  bundled into the jar) are not hashed, and gitignored files are copied by the `copytree` in
  `execute()` of `matsim.runtime.eqasim` but not hashed; the old mtime rule did not see them
  either. After editing such a file, force the rebuild by deleting the stage's
  `matsim.runtime.eqasim__<hash>.p` file (or its `.cache` directory) in the pipeline working
  directory: synpp 1.6.2 (`synpp/pipeline.py`, devalidation step "cache is not existant")
  marks a stage stale when either is missing, and with it every descendant stage.
- **Non-git sources fail loudly.** A copy of the Java tree without `.git` can no longer be used
  as `eqasim_source_path`; the stage raises instead of choosing a key.
- **Cache sharing.** `braunschweig.freight.extraction_tool` is not in `cache_share_stages`,
  and neither was its former parent `matsim.runtime.eqasim`. The coherence of shared cache
  entries is handled in Phase 4 of #468 (spec section 4.5), not here.

## Evidence

- Unit tests committed with the change: `tests/test_eqasim_runtime_token.py` (digest stable
  under mtime changes, changes on a content edit and on an untracked source file, ignores
  `target/` and `.claude/` and non-source files, raises outside a git work tree, token shape of
  the source and jar branches), `tests/test_freight_extraction_tool.py` (the tool stage) and
  `tests/test_freight_extraction.py` (`configure` depends on the tool stage and not on
  `matsim.runtime.eqasim`; `tool_java_invocation`; `execute` runs the tool entry point with the
  tool jar as class path). These pin the wiring and the token rules. Added in the review fixes:
  CRLF and LF checkouts give the same source digest, `.xml` and `.properties` edits change it, a
  tracked-but-deleted file is skipped, `eqasim_path` takes precedence over `eqasim_source_path`
  in `validate()` as in `execute()`, a missing fork pom raises an actionable error, and the tool
  stage declares the lockstep keys with `eqasim_source_path` volatile. Added in the final
  review: the source-root, empty-listing, non-UTF-8, `git_binary` and `dependency-reduced-pom.xml`
  rules of the digest, and the version log lines of the tool stage's `execute`.
  The one test that builds the jar with Maven is opt-in (`EQASIM_BS_FREIGHT_TOOL_BUILD=1`) and
  skipped by default; none runs the routing.
- Cause of the recompute: run manifest `docs/runs/freight-boundary-links-ab-1pct-2026-10-16.yml`
  (ON arm, freight extraction 05:33 to 06:48).
- File counts: measured 2026-10-21 as described under Context; the new-rule count is
  `git ls-files --cached --others --exclude-standard` filtered to the three suffixes and to
  paths without a `target` or `.claude` directory.
- **Pending, not done:** the server comparison. On the 1 % scenario, the new
  `braunschweig.freight.extraction` cache entry must be compared with the previous one
  (`freight_extraction_summary.csv`, trip counts per category), and the stale-stage explainer
  (`scripts/explain_stale_stages.py`, the Phase 1 deliverable of #468, on main since PR #469;
  spec section 4.6, usable directly for this step) must show only the expected stages as stale
  for their own reason. It will be recorded in a
  run manifest `docs/runs/freight-tool-jar-1pct-<date>.yml` (classification smoke). Until that
  manifest exists, it is not established that the tool jar reproduces the counts of the fork
  jar, and a smoke would not validate the routing in any case.
