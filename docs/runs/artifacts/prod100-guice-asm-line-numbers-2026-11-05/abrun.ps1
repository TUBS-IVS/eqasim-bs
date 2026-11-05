# Scratch: one more Melun run of a given classpath state into its own directory (noise baseline).
# Usage: powershell -File abrun.ps1 <state: before|after> <dir-name>
param([string]$State, [string]$Name)
$ErrorActionPreference = "Continue"
$sc = "C:\Users\bienzeisler\AppData\Local\Temp\c5f"
$repo = "C:\Users\bienzeisler\Documents\GitHub\ejbs-c5f"
$java = "C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot\bin\java.exe"
$ab = "$sc\ab"
$dir = "$ab\$Name"
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$dir\melun_test" | Out-Null
Copy-Item -Recurse "$repo\core\src\main\resources\melun" "$dir\melun_test\input"
Push-Location $dir
$sw = [Diagnostics.Stopwatch]::StartNew()
& $java "@$ab\args-$State.txt" 'org.eqasim.TestSimulationPipeline$TestRunSimulation' --config-path melun_test/input/config.xml --config:controler.outputDirectory melun_test/output --config:controler.lastIteration 2 *> "$dir\run.log"
$code = $LASTEXITCODE
$sw.Stop()
Pop-Location
$w = (Select-String -Path "$dir\run.log" -Pattern "Failed loading line numbers" -SimpleMatch | Measure-Object).Count
"{0}/{1}: exit={2} wall={3:N1}s 'Failed loading line numbers' lines={4}" -f $State, $Name, $code, $sw.Elapsed.TotalSeconds, $w
