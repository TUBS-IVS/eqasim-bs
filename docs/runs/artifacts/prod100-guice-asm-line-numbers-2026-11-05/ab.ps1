# Scratch A/B of the C5f investigation: the same eqasim test code on the old classpath (default Guice jar) and the new one
# (unshaded Guice classes + ASM 9.10.1). Not part of the repository.
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$sp = "C:\Users\BIENZE~1\AppData\Local\Temp\claude\e1fcf8f3dd7dc277\dc6e5f51-3c45-4cc4-a1fd-30dfd0a24403\scratchpad"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$jdk = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$java = "$jdk\bin\java.exe"
$javac = "$jdk\bin\javac.exe"
$ab = "$sc\ab"
Remove-Item -Recurse -Force $ab -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$ab\classes" | Out-Null

function Write-ArgFile($state) {
    $cp = (Get-Content "$sc\cp-$state-core.txt" -Raw).Trim()
    $full = ("$ab\classes;$repo\core\target\classes;$repo\core\target\test-classes;$cp") -replace '\\', '/'
    Set-Content -Path "$ab\args-$state.txt" -Value @("-Xmx2g", "-cp", "`"$full`"") -Encoding ascii
}
Write-ArgFile "before"
Write-ArgFile "after"

# the scratch harness is compiled once; its bytecode does not depend on the Guice variant
$cpAfter = ("$repo\core\target\classes;$repo\core\target\test-classes;" + (Get-Content "$sc\cp-after-core.txt" -Raw).Trim()) -replace '\\', '/'
Set-Content -Path "$ab\javac-args.txt" -Value @("-d", (($ab -replace '\\', '/') + "/classes"), "-cp", "`"$cpAfter`"") -Encoding ascii
& $javac "@$ab\javac-args.txt" "$sp\C5fTiming.java" 2>&1 | Out-String

Write-Output "=== 1. injector build time (fresh JVM per run, interleaved), warning lines"
$res = @{ before = @(); after = @() }
$warn = @{ before = 0; after = 0 }
$iterations = if ($env:C5F_SKIP_TIMING) { @() } else { 1..5 }
foreach ($i in $iterations) {
    foreach ($state in "before", "after") {
        $out = & $java "@$ab\args-$state.txt" org.eqasim.mode_choice.C5fTiming 2>&1 | Out-String
        if ($out -match 'C5F_INJECTOR_MS=(\d+)') { $res[$state] += [int]$Matches[1] }
        $warn[$state] += ([regex]::Matches($out, 'Failed loading line numbers')).Count
    }
}
foreach ($state in "before", "after") {
    if ($res[$state].Count -eq 0) { continue }
    $sorted = $res[$state] | Sort-Object
    "{0,-6}: runs(ms) = {1}; median = {2} ms; 'Failed loading line numbers' lines in 5 JVMs = {3}" -f $state, ($res[$state] -join ", "), $sorted[2], $warn[$state]
}

Write-Output "=== 2. Melun simulation, 2 iterations, TestConfigurator (core test fixture)"
foreach ($state in "before", "after") {
    $dir = "$ab\melun-$state"
    New-Item -ItemType Directory -Force "$dir\melun_test" | Out-Null
    Copy-Item -Recurse "$repo\core\src\main\resources\melun" "$dir\melun_test\input"
    Push-Location $dir
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $java "@$ab\args-$state.txt" 'org.eqasim.TestSimulationPipeline$TestRunSimulation' --config-path melun_test/input/config.xml --config:controler.outputDirectory melun_test/output --config:controler.lastIteration 2 *> "$dir\run.log"
    $code = $LASTEXITCODE
    $sw.Stop()
    Pop-Location
    $w = (Select-String -Path "$dir\run.log" -Pattern "Failed loading line numbers" -SimpleMatch | Measure-Object).Count
    "{0,-6}: exit={1} wall={2:N1}s 'Failed loading line numbers' lines={3}" -f $state, $code, $sw.Elapsed.TotalSeconds, $w
}

function Get-Hashes($dir) {
    $h = @{}
    $root = "$dir\melun_test\output\"
    Get-ChildItem -Recurse -File "$dir\melun_test\output" | Where-Object { $_.Name -notmatch '^logfile' } | ForEach-Object {
        $h[$_.FullName.Substring($root.Length)] = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash
    }
    return $h
}
$hb = Get-Hashes "$ab\melun-before"
$ha = Get-Hashes "$ab\melun-after"
"output files (without logfile*): before={0}, after={1}" -f $hb.Count, $ha.Count
$onlyB = @($hb.Keys | Where-Object { -not $ha.ContainsKey($_) })
$onlyA = @($ha.Keys | Where-Object { -not $hb.ContainsKey($_) })
$differ = @($hb.Keys | Where-Object { $ha.ContainsKey($_) -and $ha[$_] -ne $hb[$_] })
"only before: $($onlyB.Count); only after: $($onlyA.Count); same name, different SHA-256: $($differ.Count)"
$differ | Sort-Object | ForEach-Object { "  differs: $_" }
$same = @($hb.Keys | Where-Object { $ha.ContainsKey($_) -and $ha[$_] -eq $hb[$_] })
"identical SHA-256: $($same.Count) files"
foreach ($k in "output_plans.xml.gz", "output_events.xml.gz", "eqasim_trips.csv.gz", "eqasim_legs.csv.gz", "scorestats.csv", "modestats.csv") {
    if ($hb.ContainsKey($k)) { "  {0,-24} before={1} after={2}" -f $k, $hb[$k].Substring(0, 16), $ha[$k].Substring(0, 16) }
}
