# Scratch of the C5f investigation: the red/green evidence of the new tests in five states of the eqasim-java-bs poms, and the
# class path of the first draft (without the Guava exclusion). The final poms are saved first and restored in a finally block.
# The baseline poms are read from origin/main (git show), so the index is never touched.
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$sp = "C:\Users\BIENZE~1\AppData\Local\Temp\claude\e1fcf8f3dd7dc277\dc6e5f51-3c45-4cc4-a1fd-30dfd0a24403\scratchpad"
$stage = "$sp\artifacts"
$ev = "$sc\evidence2"
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
$both = "GuiceSourceLinesTest,GuiceClasspathTest"

try {
    Write-Output "== 1. final poms"
    Run-Tests $both "$ev\final-tests.log"

    Write-Output "== 2. baseline poms (origin/main c4051c167)"
    cmd /c "git show origin/main:pom.xml > pom.xml"
    cmd /c "git show origin/main:braunschweig/pom.xml > braunschweig\pom.xml"
    Run-Tests $both "$ev\baseline-tests.log"
    Restore-Final

    Write-Output "== 3. mutation: the simwrapper exclusion removed on purpose"
    $t = [IO.File]::ReadAllText("$repo\braunschweig\pom.xml")
    $n = [regex]::Replace($t, '(?s)(<artifactId>simwrapper</artifactId>\s*<version>\$\{matsim\.version\}</version>)\s*<!--[^>]*-->\s*<exclusions>.*?</exclusions>', '$1')
    if ($n -eq $t) { throw "simwrapper pattern did not match" }
    [IO.File]::WriteAllText("$repo\braunschweig\pom.xml", $n, $utf8)
    Run-Tests "GuiceClasspathTest" "$ev\mutation-simwrapper.log"
    Restore-Final

    Write-Output "== 4. mutation: an ASM that stops at Java 24 (asm.version 9.7.1)"
    $t = [IO.File]::ReadAllText("$repo\pom.xml")
    $n = $t.Replace("<asm.version>9.10.1</asm.version>", "<asm.version>9.7.1</asm.version>")
    if ($n -eq $t) { throw "asm.version pattern did not match" }
    [IO.File]::WriteAllText("$repo\pom.xml", $n, $utf8)
    Run-Tests $both "$ev\mutation-old-asm.log"
    Restore-Final

    Write-Output "== 5. first draft: the Guava exclusion removed, class path of core and braunschweig"
    $t = [IO.File]::ReadAllText("$repo\pom.xml")
    $n = [regex]::Replace($t, '(?s)(<classifier>classes</classifier>)\s*<!--.*?-->\s*<exclusions>.*?</exclusions>', '$1')
    if ($n -eq $t) { throw "guava pattern did not match" }
    [IO.File]::WriteAllText("$repo\pom.xml", $n, $utf8)
    powershell -NoProfile -File "$sp\cpstate.ps1" noguava | Out-Null
    Restore-Final
}
finally {
    Restore-Final
    Write-Output ("restored; diff stat of the poms against HEAD: " + ((git diff --stat HEAD -- pom.xml braunschweig/pom.xml) -join " | "))
}

function Pick($log, $pattern) {
    Select-String -Path $log -Pattern $pattern | ForEach-Object { $_.Line.Substring(0, [Math]::Min(420, $_.Line.Length)) }
}
$pat = 'Tests run:.*(GuiceSourceLinesTest|GuiceClasspathTest)|<<< (FAILURE|ERROR)|AssertionError|WARNUNG: Failed loading|^java.lang.IllegalArgumentException|copies of the|cannot read the class files|COMPILATION ERROR|package org.objectweb.asm does not exist|BUILD (SUCCESS|FAILURE)|Reactor Summary|core \.\.\.|braunschweig \.\.\.'
$out = @()
foreach ($item in @(
        @("final poms (the change), JDK 25.0.4.1, both test classes", "final-tests.log"),
        @("baseline poms (origin/main c4051c167), both test classes. asm is not on the baseline class path: the test that reads a class file with ASM errors with NoClassDefFoundError (Maven reused the test class compiled before, 'Nothing to compile'; a clean build stops at test-compile)", "baseline-tests.log"),
        @("final poms with the simwrapper exclusion removed on purpose (the braunschweig test class only)", "mutation-simwrapper.log"),
        @("final poms with an ASM that stops at Java 24 (asm.version 9.7.1), both test classes", "mutation-old-asm.log"))) {
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
