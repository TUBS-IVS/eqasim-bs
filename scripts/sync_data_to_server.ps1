<#
.SYNOPSIS
    Upload the local eqasim-data/data raw-input tree to the Linux run server.

.DESCRIPTION
    Code is distributed via git and must NOT be copied with this script. Only the
    large, gitignored raw input data (eqasim-data/data) is transferred here.

    The script prefers rsync (incremental: only changed bytes are sent, and an
    interrupted transfer can be resumed) and runs it through WSL when available,
    because rsync is not native to Windows. If rsync/WSL is not available it
    falls back to scp, which always copies the whole tree.

    The huge synpp caches (eqasim-data/cache_*) are intentionally NOT uploaded:
    they bake in absolute paths and config hashes and are rebuilt on the server.

.PARAMETER ServerUser
    SSH user on the run server. Default: environment variable EQASIM_SERVER_USER;
    if neither is set, ssh takes the user from ~/.ssh/config.

.PARAMETER ServerHost
    Host name, IP or ~/.ssh/config alias of the run server. Default: environment
    variable EQASIM_SERVER_HOST. Required: the script stops if neither is set
    (see scripts/server_target.ps1).

.PARAMETER RemoteRepo
    Path of the cloned repository on the server. Default: ~/eqasim-bs

.EXAMPLE
    ./scripts/sync_data_to_server.ps1
    ./scripts/sync_data_to_server.ps1 -ServerHost <host-or-ssh-alias> -ServerUser <user>
#>
param(
    [string]$ServerUser = $env:EQASIM_SERVER_USER,
    [string]$ServerHost = $env:EQASIM_SERVER_HOST,
    [string]$RemoteRepo = "~/eqasim-bs"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "server_target.ps1")
$resolved   = Resolve-ServerTarget -ServerUser $ServerUser -ServerHost $ServerHost
$ServerUser = $resolved.User
$ServerHost = $resolved.Host

# Resolve the local data directory relative to this script (scripts/ -> repo root).
$repoRoot = Split-Path -Parent $PSScriptRoot
$localData = Join-Path $repoRoot "eqasim-data\data"

if (-not (Test-Path $localData)) {
    throw "Local data directory not found: $localData"
}

$remoteTarget = "$($resolved.Target):${RemoteRepo}/eqasim-data/"

# Prefer rsync via WSL (incremental, resumable). Detect WSL + rsync first.
$haveWsl = $false
try {
    $null = wsl --status 2>$null
    if ($LASTEXITCODE -eq 0) {
        wsl bash -lc "command -v rsync" *> $null
        if ($LASTEXITCODE -eq 0) { $haveWsl = $true }
    }
} catch {
    $haveWsl = $false
}

if ($haveWsl) {
    # Translate the Windows path to a WSL /mnt/<drive>/... path.
    $wslData = wsl wslpath -a "$localData"
    Write-Host "==> Syncing data with rsync (incremental) via WSL ..." -ForegroundColor Cyan
    # Trailing slash on the source copies the CONTENTS of data/ into the remote
    # eqasim-data/data/ (rsync creates 'data' as the last path element).
    wsl rsync -avP --partial "$wslData" "$remoteTarget"
}
else {
    Write-Host "==> WSL/rsync not available - falling back to scp (full copy)." -ForegroundColor Yellow
    Write-Host "    Tip: install WSL (wsl --install) for fast incremental syncs later." -ForegroundColor Yellow
    scp -r "$localData" "$remoteTarget"
}

if ($LASTEXITCODE -ne 0) {
    throw "Data upload failed (exit code $LASTEXITCODE)."
}
Write-Host "==> Data upload complete." -ForegroundColor Green
