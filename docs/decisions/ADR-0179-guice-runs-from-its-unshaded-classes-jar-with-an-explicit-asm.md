# ADR-0179 · 2026-11-05 · Guice runs from its unshaded classes jar with an explicit ASM, so it can read the Java 25 class files of MATSim and the fork

- **Status:** accepted (owner task C5f, 2026-11-05; implemented in eqasim-java-bs on branch
  `fix/guice-asm-java25-class-files`, no production run has used it yet)
- **Numbering:** 0179 checked free across all local and remote branches, the sibling worktrees and the open PRs on
  2026-11-05 (highest existing: ADR-0178).
- **Finding:** bug C5f of the findings list of the 100 % production run `prod100_20261031` (felix, launch 1): the Java
  stack trace "Unsupported class file major version 69" behind the warning "Failed loading line numbers. ASM is
  probably out of date."
- **Relates to:** ADR-0038 (the own eqasim-java-bs fork), ADR-0071 (first reading of these lines), ADR-0148 and
  ADR-0149 (the eqasim jar is keyed on the content of the Java source tree).
- **Evidence:** run manifest `docs/runs/prod100-guice-asm-line-numbers-2026-11-05.yml` holds every measurement this
  record relies on; the figures are not repeated here.

## Context

- **What was logged.** One Java stack trace per Java process that builds a MATSim injector, in the production log of
  launch 1, in launch 2 and in the September production log (counts, entry points and the first trace in the
  manifest). The first reading (ADR-0071) was a caught, non-fatal DEBUG diagnostic of MATSim's `printInjector`. The
  investigation confirms "caught and non-fatal" and corrects the rest.
- **Cause chain.**
  1. MATSim 2026 and the fork are compiled for Java 25 (class file major version 69).
  2. Guice 7.0.0, the latest Guice release, embeds ASM 9.5 inside its jar (`com.google.inject.internal.asm`). That ASM
     reads class files up to Java 21. The first ASM release that reads Java 25 is 9.8.
  3. Guice reads the source file and the line of every `@Provides` method from the class file with that ASM
     (`LineNumbers`), whenever the text of a binding, a Guice error message or the module graph needs it.
  4. MATSim's `Injector.printInjector` builds the text of every org.matsim binding in every JVM that creates a
     controller injector, whatever the log level: only its last call, the log call, is DEBUG-gated, while its INFO line
     says "output skipped". So the lookup runs in every such JVM and fails for every provider method.
  5. Guice catches the exception, falls back to "Unknown Source" and logs the first failure of a JVM with the stack
     trace: one trace per JVM, so the count is the number of JVM launches that build an injector, not the number of
     failures.
- **What depends on the lookup.** The text of provider-method bindings, Guice error messages and the `modules.dot`
  graph that MATSim writes into the output directory. The binding set is the same with and without a working lookup,
  and the simulation outputs are byte-identical where they are deterministic at all (manifest). So the trace is not
  a model-behaviour defect. It is a diagnostic that fails silently: a real Guice error under Java 25 would also come
  without source lines.

## Decision

1. **The default Guice jar is replaced by the unshaded `classes` jar of the same version plus an explicit
   `org.ow2.asm:asm` of at least 9.8.** The `classes` jar holds the same Guice classes without the embedded ASM; the
   classes that read class files use the plain `org.objectweb.asm` API of the asm jar. The parent pom of eqasim-java-bs
   owns the pinned versions and the exclusion list (its comment at the Guice dependency).
2. **The default jar is excluded wherever MATSim comes into the build.** It arrives along many dependency paths (every
   MATSim contrib depends on it), so it is excluded from the MATSim dependencies of the parent pom and from the contribs
   of the braunschweig pom. Guice's own request for an older Guava is excluded as well: as a direct dependency declared
   first, Guice would win the version mediation and silently downgrade the Guava of the build (found by diffing the
   classpath before and after; manifest).
3. **Two test classes keep it that way.** `GuiceSourceLinesTest` (core) fails when a `@Provides` method of the real
   eqasim injector, or of a class compiled for this build, has no source line. `GuiceClasspathTest` (braunschweig, the
   module whose standalone jar runs) fails when the classpath holds a second copy of the Guice classes or of the ASM
   reader, a Guice with an embedded ASM, or an ASM that cannot read the class files of this build. They fail on the old
   poms and with a deliberately too old ASM (manifest). They guard these two modules only: the other modules and the
   shaded jar are not tested.
4. **One build rule keeps the Guice versions together.** The Guice core classes come from `guice.version`, the Guice
   extensions (`guice-grapher`, `guice-assistedinject`) from MATSim's own pin. A `requireSameVersions` rule of the
   maven-enforcer plugin in the parent pom stops the build of every module when they differ, so a MATSim release on
   another Guice cannot slip through (raised by the Claude review of the Java pull request).

## Rationale

- It removes the cause (a reader that cannot read the class files it is given) with published artifacts of the Guice
  version MATSim already uses, so the Guice classes, the bindings and the simulation do not change; the lookups now
  succeed. The line numbers return in binding texts, error messages and `modules.dot`.
- **Rejected: a newer Guice.** None exists; 7.0.0 is the latest release (manifest, Maven Central metadata).
- **Rejected: a log filter** (a level or handler for Guice's `LineNumbers` logger, or dropping the lines in the
  launcher). It hides the message and leaves the failure: binding texts, error messages and the module graph keep
  "Unknown Source". The project forbids hidden failures (CLAUDE.md "Fallback transparency").
- **Rejected: the Guice flag `guice_include_stack_traces`.** It does not reach the lookup: `StackTraceElements.forMember`
  has no flag check in 7.0.0 (read in the source of the tag). A flag that did would remove diagnostics, not the cause.
- **Rejected: compiling the fork for Java 21.** MATSim's own classes are Java 25, as are the newer MATSim snapshots
  found in the local repository, so the failing classes are not the fork's alone (manifest).
- **Rejected: an older JDK** (the brief's option d, to be proposed, not done). MATSim 2026 cannot run on it, so it means
  MATSim 2025 and a changed simulation. An owner decision if it is ever wanted.
- **Rejected: a newer MATSim.** The newer snapshots have the same class file version, and a MATSim bump changes the
  simulation.
- **Rejected: patching library internals** (a shadowing `LineNumbers` or `Injector` class in the jar, or a second ASM
  relocated into the package of the embedded copy, whose classes carry a `$` name prefix from Guice's own jarjar rule
  and are not a plain package relocation). Each is a private fork of a library class or depends on a naming trick; a
  published, supported artifact is simpler.
- **Rejected: managing the exclusions in `dependencyManagement` for every MATSim coordinate, or banning the default
  jar with the enforcer plugin.** More machinery than tests that fail loudly and name the offender (the classpath
  test printed both jars when a contrib exclusion was removed on purpose; manifest). The price is the limit named in
  decision 3: a contrib added to a module without a test is not caught. The enforcer plugin is used for the one rule of
  decision 4, where no test can see the failure.
- **Rejected: waiting for upstream.** No Guice release since 2023. The trigger in MATSim (`printInjector` builds its
  strings regardless of the level) is a proposal for matsim-libs: a draft is in the shared notes of the session group,
  not filed (it needs the owner's yes).

## Consequences

- **A jar rebuild.** The change is in the Java source tree, so `matsim.runtime.eqasim` rebuilds on the next run
  (ADR-0148) and its consumers recompute (ADR-0149; measured cost of the stages below the jar: the manifest
  `docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml`). Land it with the next rebuild that is due anyway (the DMC
  trip cache fix, eqasim-java-bs #59, also needs one), so that the recompute is paid once.
- **The first build on a machine downloads two artifacts** (the classes jar and the asm jar) from Maven Central.
  felix reaches Central (manifest).
- **Maintenance rule.** A MATSim contrib added to a pom needs the same Guice exclusion; `GuiceClasspathTest` names the
  second copy otherwise (in its module). The two version pins follow their owners: `asm.version` stays at 9.8 or newer,
  and `guice.version` equals the Guice version of the MATSim release (the enforcer rule of decision 4 stops the build
  otherwise, and the MATSim bump has to move the pin). The enforcer plugin needs Maven 3.6.3, the version on felix. A
  project that uses `org.eqasim:core` as a library excludes the default jar from its own MATSim dependencies in the same
  way.
- **One pull request pair.** The pom comments of eqasim-java-bs point to this record, so the two pull requests belong
  together: merge this one with, or before, the Java change.
- **Revisit** when a Guice release embeds ASM 9.8 or newer, or MATSim stops building the binding texts eagerly: the
  replacement can then go.
- **Guice also writes classes with this ASM.** Every injector build generates fast-constructor classes with it, and it is
  now the explicit asm jar instead of the embedded 9.5. The build generates the same number of classes with both, and
  the A/B simulation and the test suites run through it without a difference (manifest). Reading and writing now use the
  same, current ASM.

## Evidence

- Run manifest `docs/runs/prod100-guice-asm-line-numbers-2026-11-05.yml`: the log counts and the first trace from felix,
  the class file versions and ASM levels of every jar involved, the red and green tests, the classpath difference, the
  A/B simulation on the core test fixture, the standalone jar and the scripts. It is a wiring proof and an A/B of the
  Java build, not a validation of the model.
- The change: eqasim-java-bs branch `fix/guice-asm-java25-class-files` (the parent pom, the braunschweig pom and the two
  tests).
