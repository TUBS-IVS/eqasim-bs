"""Build the small MATSim contrib tool jar that the freight extraction runs.

The freight routing depends on MATSim's ``ExtractRelevantFreightTrips`` command, the pinned
MATSim version and the inputs -- never on the eqasim fork's own code. Keying the jar on the
whole Java source tree (``matsim.runtime.eqasim``) recomputed the routing after every Java
edit, checkout or copy (one observed case: 1 h 15 min, see ADR-0148). This
stage renders a minimal pom around ``org.matsim.contrib:application:${matsim.version}`` plus
the four-line wrapper in ``braunschweig/freight/java`` and shades it; its token is the
version string and the content of those two files. The MATSim version and the compiler
level are read from the fork's root pom so the two stay in lockstep.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil

import matsim.runtime.java as java
import matsim.runtime.maven as maven

logger = logging.getLogger(__name__)

ENTRY_POINT = "org.eqasim.braunschweig.freight.RunExtractFreightTrips"
JAR_RELATIVE = "freight-tool/target/freight-tool-%s.jar"
WRAPPER_SOURCE = os.path.join(os.path.dirname(__file__), "java", "RunExtractFreightTrips.java")
WRAPPER_TARGET = "src/main/java/org/eqasim/braunschweig/freight/RunExtractFreightTrips.java"

# Byte-reproducible jar (issue #474): early cutoff (ADR-0149) spares the routing behind this
# stage only if a rebuild from the same inputs reproduces the stage's output hash. Without a
# fixed Maven project.build.outputTimestamp the jar and shade plugins stamp the module's own
# entries (wrapper class, manifest, pom.properties) with the build time. The value is an
# arbitrary fixed instant, not the build date; it is part of the rendered pom and therefore
# of this stage's token, so changing it rebuilds the jar once.
OUTPUT_TIMESTAMP = "2026-01-01T00:00:00Z"
# Maven's incremental-build state (maven-status/*/inputFiles.lst) lists the absolute paths of
# the compiled sources, so it differs between working directories while the jar is identical.
# No consumer reads it; the jar stays hashed.
_OUTPUT_HASH_EXCLUDE = ("*/target/maven-status/*",)

# The plugins that shape the jar (compiler, resources, jar, shade) are pinned here (the
# resources and jar versions mirror the eqasim-java-bs fork root pom) instead of inheriting
# the installed Maven's super-POM defaults. Maven 3.6.3, as installed on the run server, binds
# maven-compiler-plugin 3.1, which ignores the property maven.compiler.release (supported from
# 3.6) and compiles for Java 5. Only the compiler pin fixed that failure; resources and jar are
# precautionary. The property stays as a second statement of the compiler level; the plugin's
# <release> is the one that takes effect. See ADR-0148 Decision item 8.
POM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
	<modelVersion>4.0.0</modelVersion>
	<groupId>org.eqasim.braunschweig</groupId>
	<artifactId>freight-tool</artifactId>
	<version>{matsim_version}</version>
	<properties>
		<maven.compiler.release>{java_release}</maven.compiler.release>
		<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
		<project.build.outputTimestamp>{output_timestamp}</project.build.outputTimestamp>
	</properties>
	<repositories>
		<repository>
			<id>matsim</id>
			<url>https://repo.matsim.org/repository/matsim</url>
		</repository>
		<repository>
			<id>osgeo</id>
			<name>Geotools repository</name>
			<url>https://repo.osgeo.org/repository/release/</url>
		</repository>
	</repositories>
	<dependencies>
		<dependency>
			<groupId>org.matsim.contrib</groupId>
			<artifactId>application</artifactId>
			<version>{matsim_version}</version>
		</dependency>
	</dependencies>
	<build>
		<plugins>
			<plugin>
				<groupId>org.apache.maven.plugins</groupId>
				<artifactId>maven-compiler-plugin</artifactId>
				<version>3.13.0</version>
				<configuration>
					<release>{java_release}</release>
				</configuration>
			</plugin>
			<plugin>
				<groupId>org.apache.maven.plugins</groupId>
				<artifactId>maven-resources-plugin</artifactId>
				<version>3.5.0</version>
			</plugin>
			<plugin>
				<groupId>org.apache.maven.plugins</groupId>
				<artifactId>maven-jar-plugin</artifactId>
				<version>3.5.0</version>
			</plugin>
			<plugin>
				<groupId>org.apache.maven.plugins</groupId>
				<artifactId>maven-shade-plugin</artifactId>
				<version>3.6.2</version>
				<executions>
					<execution>
						<phase>package</phase>
						<goals><goal>shade</goal></goals>
						<configuration>
							<transformers>
								<transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer" />
								<transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
									<mainClass>{entry_point}</mainClass>
								</transformer>
							</transformers>
							<filters>
								<filter>
									<artifact>*:*</artifact>
									<excludes>
										<exclude>META-INF/*.SF</exclude>
										<exclude>META-INF/*.DSA</exclude>
										<exclude>META-INF/*.RSA</exclude>
									</excludes>
								</filter>
							</filters>
						</configuration>
					</execution>
				</executions>
			</plugin>
		</plugins>
	</build>
</project>
"""


def read_pom_property(pom_path: str, name: str) -> str:
    with open(pom_path, encoding="utf-8") as handle:
        match = re.search(r"<%s>\s*([^<\s]+)\s*</%s>" % (re.escape(name), re.escape(name)), handle.read())
    if match is None:
        raise RuntimeError("property <%s> missing in %s; the freight tool reads the MATSim version and "
                           "the compiler level from the fork pom to stay in lockstep" % (name, pom_path))
    return match.group(1)


def render_pom(matsim_version: str, java_release: str) -> str:
    return POM_TEMPLATE.format(matsim_version=matsim_version, java_release=java_release,
                               entry_point=ENTRY_POINT, output_timestamp=OUTPUT_TIMESTAMP)


def _fork_pom(context) -> str:
    source_path = context.config("eqasim_source_path")
    if source_path == "":
        raise RuntimeError("eqasim_source_path is empty: the freight tool needs the fork pom to read "
                           "matsim.version and maven.compiler.source")
    return os.path.join(source_path, "pom.xml")


def _versions(context) -> tuple:
    pom = _fork_pom(context)
    if not os.path.isfile(pom):
        raise RuntimeError("fork pom not found at %s: eqasim_source_path (%s) must point at the "
                           "eqasim-java-bs work tree whose root pom.xml carries matsim.version and "
                           "maven.compiler.source" % (pom, context.config("eqasim_source_path")))
    override = context.config("freight_tool_matsim_version")
    matsim_version = override if override else read_pom_property(pom, "matsim.version")
    return matsim_version, read_pom_property(pom, "maven.compiler.source")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def configure(context):
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.maven")
    # execute() calls maven.run(); declare its options here (synpp scopes config per stage).
    java.configure(context)
    maven.configure(context)
    # Volatile: the token carries the pom's MATSim version and Java release, so the path is
    # only a locator and a different path string must not re-key this stage (ADR-0148).
    context.config("eqasim_source_path", "", volatile = True)
    # Empty = the fork pom's ${matsim.version}; set only to pin the tool to another release.
    context.config("freight_tool_matsim_version", "")


def validate(context):
    matsim_version, java_release = _versions(context)
    # Text mode normalises line endings, so the token is identical for LF and CRLF checkouts.
    with open(WRAPPER_SOURCE, encoding="utf-8") as handle:
        wrapper = handle.read()
    return {"format": 1, "matsim_version": matsim_version, "java_release": java_release,
            "wrapper": _sha256_text(wrapper), "pom": _sha256_text(render_pom(matsim_version, java_release))}


def _log_resolved_versions(context, matsim_version, java_release):
    """Log the versions the tool jar is built with, and an explicit override of the pom value."""
    pom = _fork_pom(context)
    logger.info("[freight.extraction_tool] building the tool jar with MATSim version %s, "
                "Java release %s (fork pom %s)", matsim_version, java_release, pom)
    override = context.config("freight_tool_matsim_version")
    if override:
        pom_version = read_pom_property(pom, "matsim.version")
        if override == pom_version:
            logger.info("[freight.extraction_tool] freight_tool_matsim_version override %s matches "
                        "the fork pom's matsim.version", override)
        else:
            logger.warning("[freight.extraction_tool] freight_tool_matsim_version override %s replaces "
                           "the fork pom's matsim.version %s", override, pom_version)


def execute(context):
    matsim_version, java_release = _versions(context)
    _log_resolved_versions(context, matsim_version, java_release)
    project = os.path.join(context.path(), "freight-tool")
    if os.path.exists(project):
        shutil.rmtree(project)
    os.makedirs(os.path.dirname(os.path.join(project, WRAPPER_TARGET)))
    with open(os.path.join(project, "pom.xml"), "w", encoding="utf-8") as handle:
        handle.write(render_pom(matsim_version, java_release))
    shutil.copy(WRAPPER_SOURCE, os.path.join(project, WRAPPER_TARGET))
    maven.run(context, ["package", "-DskipTests=true"], cwd=project)
    jar = JAR_RELATIVE % matsim_version
    if not os.path.exists(os.path.join(context.path(), jar)):
        raise RuntimeError("freight tool jar not built: expected %s under %s; see the maven output above"
                           % (jar, context.path()))
    return jar
