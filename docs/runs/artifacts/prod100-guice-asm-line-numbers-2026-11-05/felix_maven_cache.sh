#!/usr/bin/env bash
# Read-only look at the Maven cache of felix and at Maven Central from felix (C5f): does a first build with the changed
# poms need the network for the two new artifacts? Starts nothing, writes nothing, downloads nothing (HEAD requests only).
# Run as: ssh felix 'bash -s' < felix_maven_cache.sh
export LC_ALL=C
echo "== Guice in ~/.m2 on felix"
ls -la $HOME/.m2/repository/com/google/inject/guice/ 2>&1 | head -8
ls $HOME/.m2/repository/com/google/inject/guice/7.0.0/ 2>&1 | head -12
echo "== ASM versions in ~/.m2 on felix"
ls $HOME/.m2/repository/org/ow2/asm/asm/ 2>&1 | head -20
echo "== settings.xml and mirrors"
ls -la $HOME/.m2/settings.xml 2>&1 | head -2
grep -E '<mirror>|<url>|<offline>|mirrorOf' $HOME/.m2/settings.xml 2>/dev/null | head -10
echo "== HTTP status of HEAD requests from felix to Maven Central"
for u in "https://repo.maven.apache.org/maven2/org/ow2/asm/asm/9.10.1/asm-9.10.1.pom" "https://repo.maven.apache.org/maven2/com/google/inject/guice/7.0.0/guice-7.0.0-classes.jar"; do
  printf '%s -> ' "$u"; curl -sS -m 15 -o /dev/null -w '%{http_code}\n' -I "$u" 2>&1 | tail -1
done
