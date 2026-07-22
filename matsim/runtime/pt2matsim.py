import subprocess as sp
import os, os.path

import matsim.runtime.git as git
import matsim.runtime.java as java
import matsim.runtime.maven as maven

def configure(context):
    context.stage("matsim.runtime.git")
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.maven")

    context.config("pt2matsim_version", "22.3")
    context.config("pt2matsim_branch", "v22.3")

    # synpp scopes config per stage: execute() may only read options THIS stage's
    # configure() declared (issue #229; "Config option git_binary is not requested").
    # Declare the options read via the git.run / maven.run / java.run helpers below,
    # with the helpers' own defaults.
    context.config("git_binary", "git")
    context.config("maven_binary", "mvn")
    context.config("maven_skip_tests", False)
    context.config("java_home", "")
    context.config("java_binary", "java")
    context.config("java_memory", "50G")

def run(context, command, arguments, vm_arguments=[]):
    version = context.config("pt2matsim_version")

    # Make sure there is a dependency
    context.stage("matsim.runtime.pt2matsim")

    jar_path = "%s/pt2matsim/target/pt2matsim-%s-shaded.jar" % (
        context.path("matsim.runtime.pt2matsim"), version
    )
    java.run(context, command, arguments, jar_path, vm_arguments)

def execute(context):
    version = context.config("pt2matsim_version")
    branch = context.config("pt2matsim_branch")

    # Clone repository and checkout version
    git.run(context, [
        "clone", "https://github.com/matsim-org/pt2matsim.git",
        "--branch", branch,
        "--single-branch", "pt2matsim",
        "--depth", "1"
    ])

    # Build pt2matsim
    maven.run(context, ["package", "-DskipTests=true"], cwd = "%s/pt2matsim" % context.path())
    jar_path = "%s/pt2matsim/target/pt2matsim-%s-shaded.jar" % (context.path(), version)

    # Test pt2matsim
    java.run(context, "org.matsim.pt2matsim.run.CreateDefaultOsmConfig", [
        "test_config.xml"
    ], jar_path)

    assert os.path.exists("%s/test_config.xml" % context.path())
