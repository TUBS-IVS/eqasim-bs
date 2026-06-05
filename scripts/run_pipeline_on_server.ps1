<#
.SYNOPSIS
    One command to ship code + data to the Linux run server and start the
    eqasim-bs pipeline over SSH.

.DESCRIPTION
    Run this from Windows. It performs, in order:

      1. CODE   - verifies the local repo is committed and pushes to GitHub
                  (warns and stops if there are uncommitted changes, so you
                  never run the server on a half-finished state).
      2. DATA   - syncs eqasim-data/data to the server with rsync, which only
                  transfers what is out of sync (nothing happens if it already
                  matches). Delegates to sync_data_to_server.ps1.
      3. UPDATE - over SSH: git pull on the server + conda env update if
                  environment.yml changed (scripts/update_server.sh).
      4. RUN    - launches the pipeline inside a detached tmux session so it
                  keeps running after you disconnect.

    Use -CheckOnly to do steps 1-2 as a dry run (report what is out of sync)
    without starting the pipeline - useful to confirm "is everything synced?".

.PARAMETER Config
    Pipeline config to run on the server.
    Default: config_server_braunschweig_25pct.yml

.PARAMETER ServerUser
    SSH user on the run server. Default: felix

.PARAMETER ServerHost
    Hostname or IP of the run server. Default: 134.169.42.227

.PARAMETER RemoteRepo
    Path of the cloned repository on the server. Default: ~/eqasim-bs

.PARAMETER CheckOnly
    Only check/sync code and data; do not start the pipeline.

.PARAMETER SkipData
    Skip the data sync (use when you only changed code).

.EXAMPLE
    ./scripts/run_pipeline_on_server.ps1
    ./scripts/run_pipeline_on_server.ps1 -Config config_dryrun_braunschweig.yml
    ./scripts/run_pipeline_on_server.ps1 -CheckOnly
#>
param(
    [string]$Config     = "config_server_braunschweig_25pct.yml",
    [string]$ServerUser = "felix",
    [string]$ServerHost = "134.169.42.227",
    [string]$RemoteRepo = "~/eqasim-bs",
    [switch]$CheckOnly,
    [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$server   = "${ServerUser}@${ServerHost}"

# ---------------------------------------------------------------------------
# Step 1: CODE - ensure the local checkout is committed and pushed.
# ---------------------------------------------------------------------------
Write-Host "==> [1/4] Checking local git state ..." -ForegroundColor Cyan
Push-Location $repoRoot
try {
    $dirty = git status --porcelain
    if ($dirty) {
        Write-Host "Uncommitted changes detected:" -ForegroundColor Yellow
        git status --short
        throw "Commit (or stash) your changes before running on the server, so the run is reproducible."
    }

    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    Write-Host "    Pushing branch '$branch' to origin ..."
    # git writes normal progress (e.g. "To https://...") to stderr; under
    # $ErrorActionPreference='Stop' PowerShell treats that as a terminating
    # NativeCommandError even when the push succeeds. Relax locally and gate on the
    # real exit code instead.
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    git push origin $branch 2>&1 | Write-Host
    $pushExit = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    if ($pushExit -ne 0) {
        throw "git push failed (exit $pushExit)."
    }
}
finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Step 2: DATA - sync raw inputs (rsync only moves what is out of sync).
# ---------------------------------------------------------------------------
if (-not $SkipData) {
    Write-Host "==> [2/4] Syncing input data ..." -ForegroundColor Cyan
    $syncScript = Join-Path $PSScriptRoot "sync_data_to_server.ps1"
    & $syncScript -ServerUser $ServerUser -ServerHost $ServerHost -RemoteRepo $RemoteRepo
}
else {
    Write-Host "==> [2/4] Skipping data sync (-SkipData)." -ForegroundColor Yellow
}

if ($CheckOnly) {
    Write-Host "==> Check/sync complete (-CheckOnly): pipeline NOT started." -ForegroundColor Green
    return
}

# ---------------------------------------------------------------------------
# Step 3+4: UPDATE the server checkout and LAUNCH the pipeline in tmux.
# ---------------------------------------------------------------------------
# A detached tmux session keeps the multi-hour run alive after we disconnect.
# update_server.sh does git pull + conda env update; run_pipeline.sh activates
# conda and runs synpp. Single quotes inside keep the remote shell parsing sane.
# Kill any lingering 'eqasim' tmux session (e.g. a previous crashed run still
# finishing its cleanup) before starting, so the launch never fails with
# "duplicate session". This starts a fresh run; do not use while a wanted run is live.
$remoteCmd = "cd $RemoteRepo && bash scripts/update_server.sh && " +
             "tmux kill-session -t eqasim 2>/dev/null; " +
             "tmux new-session -d -s eqasim " +
             "'bash scripts/run_pipeline.sh $Config'"

Write-Host "==> [3/4] Updating server checkout + [4/4] launching pipeline in tmux ..." -ForegroundColor Cyan
# update_server.sh's git pull writes normal progress ("From <remote>") to stderr;
# under ErrorActionPreference Stop PowerShell would raise it as a NativeCommandError
# and abort before the tmux launch even though the remote command succeeded. Relax
# locally and gate on the real exit code instead.
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
ssh $server $remoteCmd 2>&1 | Write-Host
$sshExit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($sshExit -ne 0) {
    throw "Remote launch failed (exit code $sshExit)."
}

Write-Host "==> Pipeline started on $ServerHost in tmux session 'eqasim'." -ForegroundColor Green
Write-Host ""
Write-Host "Follow it with:" -ForegroundColor Cyan
Write-Host "    ssh $server"
Write-Host "    tmux attach -t eqasim          # watch live (Ctrl-b then d to detach)"
Write-Host "    tail -f $RemoteRepo/logs/run_*.log   # or just follow the log"
