# Scratch of the C5f investigation: the red/green evidence of the two new tests in four states of the eqasim-java-bs poms,
# and the class path of the first draft (without the Guava exclusion). The final poms are saved first and restored in a finally block.
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$sp = "C:\Users\BIENZE~1\AppData\Local\Temp\claude\e1fcf8f3dd7dc277\dc6e5f51-3c45-4cc4-a1fd-30dfd0a24403\scratchpad"
$stage = "$sp\artifacts"
$ev = "$sc\evidence"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$env:MAVEN_OPTS = "-Xmx2g"
$mvn = "C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd"
New-Item -ItemType Directory -Force $ev | Out-Null
Set-Location $repo
$utf8 = New-Object Text.UTF8Encoding($false)

Copy-Item pom.xml "$ev\pom.final.xml" -Force
Copy-Item braunschweig\pom.xml "$ev\braunschweig-pom.final.xml" -Force

function Restore-Final {
    Copy-Item "$ev\pom.final.xml" "$repo\pom.xml" -Force
    Copy-Item "$ev\braunschweig-pom.final.xml" "$repo\braunschweig\pom.xml" -Force
}
function Run-Tests($tests, $log) {
    $args = @("-T", "1", "-B", "-ntp", "-pl", "braunschweig", "-am", "test", "-Dtest=$tests", "-DfailIfNoTests=false",
        "-Dsurefire.failIfNoSpecifiedTests=false", "-Dmaven.test.failure.ignore=true", "-DforkCount=1", "-DargLine=-Xmx1536m",
        "-Dmatsim.preferLocalDtds=true")
    & $mvn @args *> $log
}

try {
    # 1. the final poms
    Write-Output "== 1. final poms"
    Run-Tests "GuiceSourceLinesTest,GuiceClasspathTest" "$ev\final-4tests.log"

    # 2. the baseline poms (c4051c167): both tests must fail
    Write-Output "== 2. baseline poms"
    git checkout -- pom.xml braunschweig/pom.xml
    Run-Tests "GuiceSourceLinesTest,GuiceClasspathTest" "$ev\baseline-4tests.log"
    Restore-Final

    # 3. mutation: the simwrapper exclusion removed on purpose
    Write-Output "== 3. mutation: simwrapper exclusion removed"
    $t = [IO.File]::ReadAllText("$repo\braunschweig\pom.xml")
    $n = [regex]::Replace($t, '(?s)(<artifactId>simwrapper</artifactId>\s*<version>\$\{matsim\.version\}</version>)\s*<!--[^>]*-->\s*<exclusions>.*?</exclusions>', '$1')
    if ($n -eq $t) { throw "simwrapper pattern did not match" }
    [IO.File]::WriteAllText("$repo\braunschweig\pom.xml", $n, $utf8)
    Run-Tests "GuiceClasspathTest" "$ev\mutation-simwrapper.log"
    Restore-Final

    # 4. the first draft: the Guava exclusion removed, class path of core and braunschweig
    Write-Output "== 4. first draft: Guava exclusion removed"
    $t = [IO.File]::ReadAllText("$repo\pom.xml")
    $n = [regex]::Replace($t, '(?s)(<classifier>classes</classifier>)\s*<!--.*?-->\s*<exclusions>.*?</exclusions>', '$1')
    if ($n -eq $t) { throw "guava pattern did not match" }
    [IO.File]::WriteAllText("$repo\pom.xml", $n, $utf8)
    powershell -NoProfile -File "$sp\cpstate.ps1" noguava | Out-Null
    Restore-Final
}
finally {
    Restore-Final
    Write-Output ("restored; diff stat of the poms: " + ((git diff --stat -- pom.xml braunschweig/pom.xml) -join " | "))
}

# the artifacts
function Pick($log, $pattern) {
    Select-String -Path $log -Pattern $pattern | ForEach-Object { $_.Line.Substring(0, [Math]::Min(420, $_.Line.Length)) }
}
$pat = 'Tests run:.*(GuiceSourceLinesTest|GuiceClasspathTest)|<<< FAILURE|AssertionError|WARNUNG: Failed loading|^java.lang.IllegalArgumentException|copies of the Guice|BUILD (SUCCESS|FAILURE)|Reactor Summary|core \.\.\.|braunschweig \.\.\.'
$out = @()
foreach ($item in @(
        @("final poms (the change), JDK 25.0.4.1, both test classes", "final-4tests.log"),
        @("baseline poms (c4051c167), both test classes", "baseline-4tests.log"),
        @("final poms with the simwrapper exclusion removed on purpose (the braunschweig test only)", "mutation-simwrapper.log"))) {
    $out += "=================================================================================================="
    $out += "== " + $item[0] + "   (mvn -pl braunschweig -am test -Dtest=..., log lines that matter, cut at 420 characters)"
    $out += Pick "$ev\$($item[1])" $pat
}
$out | Set-Content -Encoding utf8 "$stage\tests_before_after.txt"

$lines = @()
foreach ($m in "core", "braunschweig") {
    $b = (Get-Content "$sc\cp-before-$m.txt" -Raw).Trim() -split ';'
    $a = (Get-Content "$sc\cp-noguava-$m.txt" -Raw).Trim() -split ';'
    $lines += "== $m (test scope class path): baseline $($b.Count) entries, first draft without the Guava exclusion $($a.Count) entries"
    $lines += "only in the BASELINE:"
    $lines += ($b | Where-Object { $_ -notin $a } | ForEach-Object { "  " + (($_ -replace [regex]::Escape("C:\Users\bienzeisler\.m2\repository\"), "~/.m2/repository/") -replace '\\', '/') })
    $lines += "only in the FIRST DRAFT:"
    $lines += ($a | Where-Object { $_ -notin $b } | ForEach-Object { "  " + (($_ -replace [regex]::Escape("C:\Users\bienzeisler\.m2\repository\"), "~/.m2/repository/") -replace '\\', '/') })
}
$lines | Set-Content -Encoding utf8 "$stage\classpath_diff_without_guava_exclusion.txt"
Write-Output "artifacts written"
