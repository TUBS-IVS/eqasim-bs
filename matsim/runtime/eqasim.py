import re
import subprocess as sp
import os, os.path, shutil

import matsim.runtime.git as git
import matsim.runtime.java as java
import matsim.runtime.maven as maven

# eqasim-java-bs sits on upstream eqasim-java v2.2.0 (ab938aaac, 2026-06-03) but keeps
# its own release line on top, so the fork's version is NOT the upstream one (see
# TUBS-IVS/eqasim-java-bs docs/versioning.md). The braunschweig module version drives the
# built jar NAME (braunschweig-<version>.jar) and execute() looks for exactly that file.
#
# For the source-build path that version is READ from the built tree's pom, NOT from this
# constant (resolve_source_version, ADR-0098): the pom owns the number, and a python copy
# of it broke the pipeline twice. The fork's release automation lists braunschweig/pom.xml
# among its release files, so every fork release renames our jar -- v2.3.0 and v2.3.1
# (2026-08-21, a pure version bump carrying no code at all) each did, and the workflow
# runs on every merge to main. Note what a release does NOT do here: the pipeline builds
# from eqasim_source_path and validate() keys its cache on the newest source mtime, so a
# plain `git pull` in that tree already delivers a java fix and triggers the rebuild.
#
# This constant remains the fallback for the two paths that have no such pom -- a prebuilt
# eqasim_path jar, and the legacy upstream clone -- and is kept current (2.3.1, the fork's
# latest release) so the fallback is not misleading. An explicit eqasim_version config
# still overrides everything.
#
# The fork targets Java 25 (upstream bumped maven.compiler source/target 21 -> 25);
# ensure the build environment provides a JDK 25 (see docs/UPGRADE_SYNPP_AND_JAVA.md).
DEFAULT_EQASIM_VERSION = "2.3.1"
DEFAULT_EQASIM_BRANCH = "main"
# Only used by the legacy upstream-clone path below, which builds upstream's bavaria
# module at this commit. That commit is upstream v2.2.0, so using the legacy path also
# requires setting eqasim_version to the UPSTREAM version explicitly -- the default
# above is the fork's version and would look for a bavaria-<fork version>.jar that
# upstream never produces. The mismatch fails loudly in execute() rather than silently.
DEFAULT_EQASIM_COMMIT = "ab938aaac"

#: Relative location of the module pom that decides the built jar's name.
BRAUNSCHWEIG_POM = os.path.join("braunschweig", "pom.xml")


def module_version_from_pom(pom_path):
    """Version the braunschweig module inherits from its ``<parent>``.

    That value names the artifact (``braunschweig-<version>.jar``), so it is the
    single source of truth for what the build produces. Read with a regex rather
    than an XML parser on purpose: the pom declares a default namespace, which
    turns every ElementTree path into namespace bookkeeping for one string, and
    the ``<parent>`` block is unambiguous. Only the parent block is searched --
    the dependency entries below it carry the same number and must not be picked
    up positionally.

    Raises RuntimeError when the file cannot be read or has no parent version;
    callers decide whether that is fatal (see :func:`resolve_source_version`).
    """
    try:
        with open(pom_path, encoding = "utf-8") as f:
            content = f.read()
    except OSError as error:
        raise RuntimeError("Cannot read the braunschweig pom at %s: %s" % (pom_path, error))

    parent = re.search(r"<parent>(.*?)</parent>", content, re.DOTALL)
    if parent is None:
        raise RuntimeError("No <parent> block in %s, so the module version is unknown" % pom_path)

    version = re.search(r"<version>\s*([^<\s]+)\s*</version>", parent.group(1))
    if version is None:
        raise RuntimeError("No <version> inside the <parent> block of %s" % pom_path)

    return version.group(1)


def resolve_source_version(source_path, configured_version, log = print):
    """Version to expect from a source build of ``source_path``.

    Precedence: an explicit ``eqasim_version`` config wins (the escape hatch for
    a deliberately pinned build), otherwise the value is READ from the module pom
    of the tree that is actually built. Keeping a copy of that number in python
    broke the pipeline twice, because the fork's release automation lists
    ``braunschweig/pom.xml`` among its release files, so every fork release
    renames our jar while delivering no code (issue #347).

    If the pom cannot be read, ``DEFAULT_EQASIM_VERSION`` is used -- but loudly,
    never silently: an unnoticed fallback here surfaces later as a confusing
    "JAR not built" abort instead of the real cause.
    """
    if configured_version:
        log("Using the configured eqasim_version %s for the source build." % configured_version)
        return configured_version

    pom_path = os.path.join(source_path, BRAUNSCHWEIG_POM)

    try:
        version = module_version_from_pom(pom_path)
    except RuntimeError as error:
        log("WARNING! Falling back to the built-in eqasim version %s: %s"
            % (DEFAULT_EQASIM_VERSION, error))
        return DEFAULT_EQASIM_VERSION

    log("Expecting braunschweig-%s.jar (version read from %s)." % (version, pom_path))
    return version


def configure(context):
    context.stage("matsim.runtime.git")
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.maven")

    # None -> derive the version from the built tree's pom (see resolve_source_version).
    # An explicit value still wins, which the legacy upstream-clone path below needs.
    context.config("eqasim_version", None)
    context.config("eqasim_branch", DEFAULT_EQASIM_BRANCH)
    context.config("eqasim_commit", DEFAULT_EQASIM_COMMIT)
    context.config("eqasim_repository", "https://github.com/eqasim-org/eqasim-java.git")
    context.config("eqasim_path", "")
    # Build OUR own editable Java project (eqasim-java-bs, the renamed braunschweig
    # module) from a local source tree instead of cloning upstream. When set, no git
    # clone happens; the source is copied in and Maven-built, so local Java edits take
    # effect on the next run (see validate(), which keys the cache on the source mtime).
    context.config("eqasim_source_path", "")

    # Same synpp per-stage config contract as pt2matsim (issue #229): delegate to the
    # helpers' own configure() so the declares cannot drift from what git.run /
    # maven.run actually read.
    git.configure(context)
    maven.configure(context)

def run(context, command, arguments):
    # The eqasim stage returns the relative path of the built/provided jar (bavaria
    # legacy clone OR our braunschweig project); use it so this works for both.
    jar_relative = context.stage("matsim.runtime.eqasim")
    jar_path = "%s/%s" % (context.path("matsim.runtime.eqasim"), jar_relative)
    java.run(context, command, arguments, jar_path)

def execute(context):
    configured_version = context.config("eqasim_version")
    eqasim_path = context.config("eqasim_path")
    source_path = context.config("eqasim_source_path")

    # Paths 1 and 3 have no braunschweig pom to read, so they keep the constant.
    version = configured_version or DEFAULT_EQASIM_VERSION

    # 1) A prebuilt jar is provided directly (mainly for unit-test inputs).
    if eqasim_path != "":
        target = "%s/eqasim-java/braunschweig/target" % context.path()
        os.makedirs(target, exist_ok = True)
        shutil.copy(eqasim_path, "%s/braunschweig-%s.jar" % (target, version))
        return "eqasim-java/braunschweig/target/braunschweig-%s.jar" % version

    # 2) Build OUR OWN editable project from a local source tree (no git clone).
    if source_path != "":
        destination = "%s/eqasim-java" % context.path()
        if os.path.exists(destination):
            shutil.rmtree(destination)
        shutil.copytree(source_path, destination,
                        ignore = shutil.ignore_patterns("target", ".git"))
        # Read the version from the COPIED tree -- that is the one maven builds, and
        # a pom change already devalidates this stage through validate()'s mtime key.
        version = resolve_source_version(destination, configured_version)
        maven.run(context, ["-Pstandalone", "--projects", "braunschweig", "--also-make",
                            "package", "-DskipTests=true"], cwd = destination)
        jar = "eqasim-java/braunschweig/target/braunschweig-%s.jar" % version
        if not os.path.exists("%s/%s" % (context.path(), jar)):
            raise RuntimeError(
                "braunschweig-%s.jar not built from eqasim_source_path %s. The expected "
                "name comes from %s; check the maven output above for the real failure."
                % (version, source_path, os.path.join(destination, BRAUNSCHWEIG_POM)))
        return jar

    # 3) Legacy: clone the upstream eqasim-java (bavaria module) and build it.
    branch = context.config("eqasim_branch")
    git.run(context, [
        "clone", "--single-branch", "-b", branch,
        context.config("eqasim_repository"), "eqasim-java"
    ])
    git.run(context, ["checkout", context.config("eqasim_commit")],
            cwd = "{}/eqasim-java".format(context.path()))
    maven.run(context, ["-Pstandalone", "--projects", "bavaria", "--also-make", "package",
                        "-DskipTests=true"], cwd = "%s/eqasim-java" % context.path())
    jar = "eqasim-java/bavaria/target/bavaria-%s.jar" % version
    if not os.path.exists("%s/%s" % (context.path(), jar)):
        raise RuntimeError("The JAR was not created correctly. Wrong eqasim_version specified?")
    return jar

def validate(context):
    # Build from our own local source: key the cache on the latest source mtime so
    # local Java edits trigger a rebuild on the next run.
    source_path = context.config("eqasim_source_path")
    if source_path != "":
        if not os.path.exists(source_path):
            raise RuntimeError("eqasim_source_path does not exist: %s" % source_path)
        latest = 0.0
        for root, _dirs, files in os.walk(source_path):
            if "target" in root or ".git" in root:
                continue
            for name in files:
                if name.endswith((".java", ".xml", ".properties")):
                    latest = max(latest, os.path.getmtime(os.path.join(root, name)))
        return latest

    path = context.config("eqasim_path")
    if path == "":
        return True
    if not os.path.exists(path):
        raise RuntimeError("Cannot find eqasim at: %s" % path)
    return os.path.getmtime(path)
