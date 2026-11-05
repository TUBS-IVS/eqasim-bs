# Scratch of the C5f investigation: build the standalone jar of eqasim-java-bs the way the stage matsim.runtime.eqasim does
# (copy of the source tree without target, .git and .claude; mvn -Pstandalone --projects braunschweig --also-make package
# -DskipTests=true -Dproject.build.outputTimestamp=2026-01-01T00:00:00Z), from the changed work tree ("after") or from the
# baseline commit origin/main ("before").
# Usage: powershell -File jarbuild.ps1 <after|before>
param([string]$State)
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$dest = "$sc\jarbuild\$State\eqasim-java"
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$env:MAVEN_OPTS = "-Xmx2g"
$mvn = "C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd"
Remove-Item -Recurse -Force "$sc\jarbuild\$State" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $dest | Out-Null
if ($State -eq "after") {
    robocopy $repo $dest /E /XD target .git .claude /NFL /NDL /NJH /NJS /NP | Out-Null
} else {
    $tar = "$sc\jarbuild\baseline-source.tar"
    git -C $repo archive --format=tar -o $tar origin/main
    tar -xf $tar -C $dest
}
"source copy: {0} files, base commit of the baseline: {1}" -f (Get-ChildItem -Recurse -File $dest | Measure-Object).Count, (git -C $repo rev-parse --short origin/main)
Set-Location $dest
$sw = [Diagnostics.Stopwatch]::StartNew()
& $mvn -T 1 -B -ntp -Pstandalone --projects braunschweig --also-make package "-DskipTests=true" "-Dproject.build.outputTimestamp=2026-01-01T00:00:00Z" *> "$sc\jarbuild\$State-build.log"
"mvn exit={0}, wall={1:N1} min" -f $LASTEXITCODE, $sw.Elapsed.TotalMinutes
$jar = "$dest\braunschweig\target\braunschweig-2.3.1.jar"
if (Test-Path $jar) { "jar: {0} ({1:N0} bytes)" -f $jar, (Get-Item $jar).Length } else { "NO JAR" }
Select-String -Path "$sc\jarbuild\$State-build.log" -Pattern "Reactor Summary|SUCCESS|FAILURE|overlapping|WARNING.*(guice|asm)" | ForEach-Object { $_.Line.Substring(0, [Math]::Min(200, $_.Line.Length)) } | Select-Object -First 14
