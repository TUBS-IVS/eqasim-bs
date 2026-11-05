# Scratch helper of the C5f investigation: write the test-scope classpath of core and braunschweig for the current poms.
# Usage: powershell -File cpstate.ps1 <state-label>   (labels the output files cp-<label>-core.txt / cp-<label>-braunschweig.txt)
param([string]$Label)
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot"
$env:MAVEN_OPTS = "-Xmx2g"
$mvn = "C:\Users\bienzeisler\tools\apache-maven-3.9.9\bin\mvn.cmd"
Set-Location $repo
& $mvn -T 1 -B -ntp -pl braunschweig -am compile dependency:build-classpath "-Dmdep.outputFile=target/cp-test.txt" "-Dmdep.includeScope=test" 2>&1 |
    Select-String -Pattern "BUILD|ERROR|Reactor Summary|SUCCESS|FAILURE" | ForEach-Object { $_.Line }
Copy-Item "$repo\core\target\cp-test.txt" "$sc\cp-$Label-core.txt" -Force
Copy-Item "$repo\braunschweig\target\cp-test.txt" "$sc\cp-$Label-braunschweig.txt" -Force
foreach ($m in "core", "braunschweig") {
    $n = ((Get-Content "$sc\cp-$Label-$m.txt" -Raw).Trim() -split ';').Count
    "$Label $m classpath entries: $n"
}
