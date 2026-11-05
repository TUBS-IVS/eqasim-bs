# Scratch of the C5f investigation: wall time of the injector build in a fresh JVM (5 interleaved runs per class path), the
# warning lines of those runs, the binding set and the @Provides methods without a source line. Uses the argument files and
# the compiled scratch harnesses of ab.ps1 (not part of the repository).
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$ab = "$sc\ab"
$jdk = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$java = "$jdk\bin\java.exe"

Write-Output "=== 1. injector build time (InjectorBuilder.build in a fresh JVM, JDK 25.0.4.1), 5 runs per class path, interleaved"
$res = @{ before = @(); after = @() }
$warn = @{ before = 0; after = 0 }
foreach ($i in 1..5) {
    foreach ($state in "before", "after") {
        $out = & $java "@$ab\args-$state.txt" org.eqasim.mode_choice.C5fTiming 2>&1 | Out-String
        if ($out -match 'C5F_INJECTOR_MS=(\d+)') { $res[$state] += [int]$Matches[1] }
        $warn[$state] += ([regex]::Matches($out, 'Failed loading line numbers')).Count
    }
}
foreach ($state in "before", "after") {
    $sorted = $res[$state] | Sort-Object
    "{0,-6}: runs (ms) = {1}; median = {2} ms; 'Failed loading line numbers' lines over the 5 JVMs = {3}" -f $state, ($res[$state] -join ", "), $sorted[2], $warn[$state]
}

Write-Output "=== 2. binding set of the real eqasim injector (2 runs per class path): number of explicit bindings and SHA-256 of the sorted keys"
foreach ($i in 1..2) {
    foreach ($state in "before", "after") {
        $o = & $java "@$ab\args-$state.txt" org.eqasim.mode_choice.C5fBindings 2>&1 | Out-String
        "{0,-6} run {1}: {2}" -f $state, $i, ([regex]::Match($o, 'C5F_BINDINGS=\d+ C5F_KEYS_SHA256=[0-9a-f]+').Value)
    }
}

Write-Output "=== 3. @Provides methods of the real eqasim injector by package, and how many have no source line"
foreach ($state in "before", "after") {
    Write-Output "-- $state"
    & $java "@$ab\args-$state.txt" org.eqasim.mode_choice.C5fProviders 2>&1 | Select-String "C5F_" | ForEach-Object { "   " + $_.Line }
}
