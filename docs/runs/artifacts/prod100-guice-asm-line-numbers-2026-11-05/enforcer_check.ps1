# Scratch of the C5f investigation: the enforcer rule that keeps the Guice jars of one version (requireSameVersions in the parent
# pom of eqasim-java-bs). Step 1: mvn validate on all modules with the final pom must pass. Step 2: with guice-grapher pinned to
# 4.2.2 on purpose (dependencyManagement, a version that is in the local repository) the build must stop. The pom is restored.
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$sp = "C:\Users\BIENZE~1\AppData\Local\Temp\claude\e1fcf8f3dd7dc277\dc6e5f51-3c45-4cc4-a1fd-30dfd0a24403\scratchpad"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$env:MAVEN_OPTS = "-Xmx2g"
$mvn = "C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd"
Set-Location $repo
$utf8 = New-Object Text.UTF8Encoding($false)
Copy-Item pom.xml "$sc\pom.enforcer.final.xml" -Force
$out = @("# mvn -T 1 -B -ntp validate   (JDK 25.0.4.1, Maven 3.9.9, maven-enforcer-plugin 3.5.0, rule requireSameVersions over com.google.inject:* and com.google.inject.extensions:*)", "")
try {
    $out += "== 1. the final pom, all modules"
    & $mvn -T 1 -B -ntp validate *> "$sc\enforcer-pass.log"
    $out += "mvn exit=$LASTEXITCODE"
    $out += (Select-String -Path "$sc\enforcer-pass.log" -Pattern "enforcer.*guice-jars-of-one-version|Rule 0|BUILD (SUCCESS|FAILURE)|Reactor Summary|SUCCESS \[|FAILURE \[" | ForEach-Object { $_.Line.Substring(0, [Math]::Min(230, $_.Line.Length)) })
    $out += ""

    $out += "== 2. mutation: guice-grapher pinned to 4.2.2 on purpose, module core"
    $t = [IO.File]::ReadAllText("$repo\pom.xml")
    $pin = "`t<dependencyManagement>`n`t`t<dependencies>`n`t`t`t<dependency>`n`t`t`t`t<groupId>com.google.inject.extensions</groupId>`n`t`t`t`t<artifactId>guice-grapher</artifactId>`n`t`t`t`t<version>4.2.2</version>`n`t`t`t</dependency>`n`t`t</dependencies>`n`t</dependencyManagement>`n`n"
    $marker = "`t<dependencies>"
    $i = $t.IndexOf($marker)
    if ($i -lt 0) { throw "dependencies marker not found" }
    [IO.File]::WriteAllText("$repo\pom.xml", $t.Insert($i, $pin), $utf8)
    & $mvn -T 1 -B -ntp -pl core validate *> "$sc\enforcer-mutation.log"
    $out += "mvn exit=$LASTEXITCODE"
    $out += (Select-String -Path "$sc\enforcer-mutation.log" -Pattern "enforcer.*guice-jars-of-one-version|Rule 0|RequireSameVersions|same version|Found|BUILD (SUCCESS|FAILURE)|ERROR\].*(Rule|version)" | ForEach-Object { $_.Line.Substring(0, [Math]::Min(330, $_.Line.Length)) } | Select-Object -First 14)
}
finally {
    Copy-Item "$sc\pom.enforcer.final.xml" "$repo\pom.xml" -Force
    $out += ""
    $out += "pom restored: " + ((Get-FileHash "$repo\pom.xml").Hash -eq (Get-FileHash "$sc\pom.enforcer.final.xml").Hash)
}
$out | Set-Content -Encoding utf8 "$sp\artifacts\enforcer_guice_versions.txt"
$out
