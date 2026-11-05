# Scratch of the C5f investigation: contents check and jar-only injector smoke of the two standalone jars built by
# jarbuild.ps1 (before = origin/main c4051c167, after = the change). Writes the artifact standalone_jar.txt.
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$sp = "C:\Users\BIENZE~1\AppData\Local\Temp\claude\e1fcf8f3dd7dc277\dc6e5f51-3c45-4cc4-a1fd-30dfd0a24403\scratchpad"
$py = "C:\Users\bienzeisler\AppData\Local\Programs\Python\Python313\python.exe"
$jdk = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$out = @()
$out += "# Standalone jar of braunschweig, built as the stage matsim.runtime.eqasim builds it: a copy of the source tree without target, .git and .claude,"
$out += "# mvn -Pstandalone --projects braunschweig --also-make package -DskipTests=true -Dproject.build.outputTimestamp=2026-01-01T00:00:00Z"
$out += "# (JDK 25.0.4.1, Maven 3.9.9, Windows). before = git archive of origin/main c4051c167; after = the changed work tree."
$out += "# Smoke: JarSmoke.java (docs artifacts) builds the controller injector of the production configurator (BraunschweigConfigurator) with ONLY the jar"
$out += "# on the class path and counts the @Provides methods whose binding text has no source line and the warning lines of the JVM."
foreach ($state in "before", "after") {
    $jar = "$sc\jarbuild\$state\eqasim-java\braunschweig\target\braunschweig-2.3.1.jar"
    $out += ""
    $out += "=================================================================================================="
    $out += "== $state"
    $out += (& $py -I "$sp\jarcheck.py" $jar 2>&1 | ForEach-Object { $_.ToString() })
    $log = "$sc\jarbuild\$state-build.log"
    $overlap = @(Select-String -Path $log -Pattern "define \d+ overlapping").Count
    $lines = Get-Content $log
    $classOverlaps = @(); $resources = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "guice-7.0.0-classes|asm-9.10.1|guice-7.0.0\.jar") {
            $j = $i + 1
            while ($j -lt $lines.Count -and $lines[$j] -match '^\[WARNING\]\s+- ') { $resources += $lines[$j].Trim(); $j++ }
        }
    }
    $out += "shade warnings in the build: $overlap; resources named under the warnings that list a Guice or asm jar: " + ((($resources | Sort-Object -Unique) -replace '^\[WARNING\]\s+- ', '') -join ", ") + " (no class)"
    $o = & "$jdk\bin\java.exe" -Xmx2g -cp "$jar;$sc\jarsmoke" JarSmoke 2>&1 | Out-String
    $out += ($o -split "`r?`n" | Select-String "C5F_JAR_SMOKE" | ForEach-Object { $_.Line })
    $out += "warning lines 'Failed loading line numbers' in that JVM: " + ([regex]::Matches($o, "Failed loading line numbers")).Count
}
$out | Set-Content -Encoding utf8 "$sp\artifacts\standalone_jar.txt"
$out
