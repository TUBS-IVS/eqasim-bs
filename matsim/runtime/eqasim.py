import subprocess as sp
import os, os.path, shutil

import matsim.runtime.git as git
import matsim.runtime.java as java
import matsim.runtime.maven as maven

# eqasim-java-bs is rebased onto upstream eqasim-java v2.2.0 (ab938aaac, 2026-06-03).
# The module version drives the built jar name (braunschweig-<version>.jar), so this
# MUST match the <version> in eqasim-java-bs/braunschweig/pom.xml. Building v2.2.0
# requires JDK 25 (upstream bumped maven.compiler source/target 21 -> 25); ensure the
# felix build environment provides a JDK 25 (see docs/UPGRADE_SYNPP_AND_JAVA.md).
DEFAULT_EQASIM_VERSION = "2.2.0"
DEFAULT_EQASIM_BRANCH = "main"
DEFAULT_EQASIM_COMMIT = "ab938aaac"

def configure(context):
    context.stage("matsim.runtime.git")
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.maven")

    context.config("eqasim_version", DEFAULT_EQASIM_VERSION)
    context.config("eqasim_branch", DEFAULT_EQASIM_BRANCH)
    context.config("eqasim_commit", DEFAULT_EQASIM_COMMIT)
    context.config("eqasim_repository", "https://github.com/eqasim-org/eqasim-java.git")
    context.config("eqasim_path", "")
    # Build OUR own editable Java project (eqasim-java-bs, the renamed braunschweig
    # module) from a local source tree instead of cloning upstream. When set, no git
    # clone happens; the source is copied in and Maven-built, so local Java edits take
    # effect on the next run (see validate(), which keys the cache on the source mtime).
    context.config("eqasim_source_path", "")

    # Same synpp per-stage config contract as pt2matsim (issue #229): declare the
    # options execute() reads via the git.run / maven.run helpers.
    context.config("git_binary", "git")
    context.config("maven_binary", "mvn")
    context.config("maven_skip_tests", False)
    context.config("java_home", "")

def run(context, command, arguments):
    # The eqasim stage returns the relative path of the built/provided jar (bavaria
    # legacy clone OR our braunschweig project); use it so this works for both.
    jar_relative = context.stage("matsim.runtime.eqasim")
    jar_path = "%s/%s" % (context.path("matsim.runtime.eqasim"), jar_relative)
    java.run(context, command, arguments, jar_path)

def execute(context):
    version = context.config("eqasim_version")
    eqasim_path = context.config("eqasim_path")
    source_path = context.config("eqasim_source_path")

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
        maven.run(context, ["-Pstandalone", "--projects", "braunschweig", "--also-make",
                            "package", "-DskipTests=true"], cwd = destination)
        jar = "eqasim-java/braunschweig/target/braunschweig-%s.jar" % version
        if not os.path.exists("%s/%s" % (context.path(), jar)):
            raise RuntimeError("braunschweig JAR not built from eqasim_source_path: %s" % source_path)
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
