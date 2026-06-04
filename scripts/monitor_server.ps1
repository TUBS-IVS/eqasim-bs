<#
.SYNOPSIS
    Live terminal dashboard for an eqasim-bs pipeline run on the Linux server.

.DESCRIPTION
    Polls the run server over SSH every few seconds and renders a refreshing
    status panel: whether the tmux run session is alive, the server load and RAM,
    the current synpp stage, the MATSim iteration (X / last) with a progress bar,
    and the tail of the live log. One SSH round-trip per refresh; nothing is
    installed on the server.

    Pairs with run_pipeline_on_server.ps1 (which starts the run in the tmux
    session 'eqasim' and logs to logs/run_*.log).

.PARAMETER ServerUser
    SSH user. Default: felix
.PARAMETER ServerHost
    Host / IP. Default: 134.169.42.227
.PARAMETER RemoteRepo
    Repo path on the server. Default: ~/eqasim-bs
.PARAMETER RefreshSec
    Seconds between refreshes. Default: 10
.PARAMETER LastIteration
    MATSim last iteration index for the progress bar (matsim_last_iteration in the
    config). Default: 99 (a 100-iteration run, 0..99).
.PARAMETER Once
    Print a single snapshot and exit (no live loop).

.EXAMPLE
    ./scripts/monitor_server.ps1
    ./scripts/monitor_server.ps1 -RefreshSec 5
    ./scripts/monitor_server.ps1 -Once
#>
param(
    [string]$ServerUser    = "felix",
    [string]$ServerHost    = "134.169.42.227",
    [string]$RemoteRepo    = "~/eqasim-bs",
    [int]   $RefreshSec    = 10,
    [int]   $LastIteration = 99,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$server = "${ServerUser}@${ServerHost}"

# Remote snippet: gather everything in one round-trip, tab-separated KEY<TAB>VALUE
# lines plus a tail block. Single-quoted here-string so PowerShell does not touch
# the bash $-variables; __REPO__ is the only substitution.
$remoteTemplate = @'
REPO=__REPO__
# Expand a leading "~" by hand: tilde expansion only happens at the start of a
# word, so once REPO is used as "$REPO/logs/..." bash would treat the tilde as a
# literal directory name. Substitute $HOME for a leading "~" explicitly.
REPO="${REPO/#\~/$HOME}"
LOG=$(ls -t $REPO/logs/run_*.log 2>/dev/null | head -1)
printf 'LOG\t%s\n' "$LOG"
if tmux has-session -t eqasim 2>/dev/null; then printf 'ALIVE\tyes\n'; else printf 'ALIVE\tno\n'; fi
printf 'NOW\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf 'LOAD\t%s\n' "$(uptime | sed 's/.*load average: //')"
printf 'MEM\t%s\n' "$(free -g | awk '/Mem:/{printf "%d / %d GB used", $3, $2}')"
printf 'NPROC\t%s\n' "$(nproc)"
if [ -n "$LOG" ]; then
  printf 'MTIME\t%s\n' "$(date -r "$LOG" '+%Y-%m-%d %H:%M:%S')"
  printf 'ITER\t%s\n' "$(grep -aoE 'ITERATION [0-9]+' "$LOG" | tail -1 | grep -oE '[0-9]+')"
  printf 'STAGE\t%s\n' "$(grep -aE 'Executing|Running|^\[' "$LOG" | tail -1 | cut -c1-110)"
  printf 'DONE\t%s\n' "$(grep -acE 'Pipeline finished|MATSim run finished' "$LOG")"
  echo '---TAIL---'
  tail -n 12 "$LOG"
fi
'@
# The here-string above uses Windows line endings (CRLF). When the script is
# piped to the remote "bash -s", every line would keep a trailing carriage
# return, so e.g. REPO=~/eqasim-bs<CR> yields the invalid path "$REPO<CR>/logs"
# and all REPO-derived fields come back empty. Normalise to LF before sending.
$remoteCmd = $remoteTemplate.Replace("__REPO__", $RemoteRepo).Replace("`r`n", "`n").Replace("`r", "`n")

function Get-Snapshot {
    # Send the remote script base64-encoded as a single ASCII ssh argument and
    # decode it remotely. This sidesteps three independent Windows-side hazards
    # at once:
    #   1. OpenSSH (Windows) re-quotes command-line arguments and mangled the
    #      embedded awk single-quote program ("syntax error at or near %").
    #   2. Piping the script to "bash -s" via stdin makes PowerShell prepend a
    #      UTF-8 BOM, so the first line became an invalid command and REPO (and
    #      every REPO-derived field) came back empty.
    #   3. CRLF line endings leave a trailing carriage return on each line.
    # base64 is pure ASCII with no shell metacharacters, so it survives intact;
    # UTF8.GetBytes emits no BOM and $remoteCmd is already normalised to LF.
    # ErrorActionPreference is relaxed locally so the client's non-fatal stderr
    # (e.g. the OpenSSH post-quantum warning) is captured as text rather than
    # raised as a terminating NativeCommandError.
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($remoteCmd))
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = ssh -o ConnectTimeout=8 $server "echo $encoded | base64 -d | bash" 2>&1
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $fields = @{}
    $tail = New-Object System.Collections.Generic.List[string]
    $inTail = $false
    foreach ($line in $raw) {
        if ($line -eq '---TAIL---') { $inTail = $true; continue }
        if ($inTail) { $tail.Add($line); continue }
        $parts = $line -split "`t", 2
        if ($parts.Count -eq 2) { $fields[$parts[0]] = $parts[1] }
    }
    return @{ Fields = $fields; Tail = $tail }
}

function Show-Dashboard($snap) {
    $f = $snap.Fields
    Clear-Host
    $alive = $f['ALIVE']
    $aliveColor = if ($alive -eq 'yes') { 'Green' } else { 'Red' }
    Write-Host "  eqasim-bs run monitor  -  $server" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * 66))
    $sessionText = if ($alive -eq 'yes') { 'RUNNING (tmux: eqasim)' } else { 'not running' }
    Write-Host -NoNewline "  session: "
    Write-Host $sessionText -ForegroundColor $aliveColor
    Write-Host "  server : load$($f['LOAD'])  |  mem $($f['MEM'])  |  $($f['NPROC']) cores"
    Write-Host "  log    : $($f['LOG'])"
    Write-Host "  updated: $($f['MTIME'])   (server now $($f['NOW']))"
    Write-Host ("  " + ("-" * 66))

    $iter = $f['ITER']
    if ($iter -ne $null -and $iter -ne '') {
        $i = [int]$iter
        $total = [math]::Max($LastIteration, 1)
        $frac = [math]::Min($i / $total, 1.0)
        $barLen = 40
        $fill = [int]([math]::Round($frac * $barLen))
        $bar = ('#' * $fill) + ('.' * ($barLen - $fill))
        Write-Host "  phase  : MATSim simulation" -ForegroundColor Yellow
        Write-Host ("  MATSim : [{0}] iteration {1} / {2}  ({3:P0})" -f $bar, $i, $total, $frac)
    }
    else {
        Write-Host "  phase  : population synthesis (synpp)" -ForegroundColor Yellow
        Write-Host "  stage  : $($f['STAGE'])"
    }
    if ($f['DONE'] -and [int]$f['DONE'] -gt 0) {
        Write-Host "  status : PIPELINE FINISHED" -ForegroundColor Green
    }
    Write-Host ("  " + ("-" * 66))
    Write-Host "  live log tail:" -ForegroundColor DarkGray
    foreach ($t in $snap.Tail) { Write-Host "    $t" -ForegroundColor DarkGray }
    Write-Host ""
    if (-not $Once) {
        Write-Host "  refresh ${RefreshSec}s  -  Ctrl-C to stop  -  live attach: ssh $server then tmux attach -t eqasim" -ForegroundColor DarkCyan
    }
}

if ($Once) {
    Show-Dashboard (Get-Snapshot)
    return
}

Write-Host "Connecting to $server ..." -ForegroundColor Cyan
while ($true) {
    try {
        Show-Dashboard (Get-Snapshot)
    }
    catch {
        Write-Host "  (poll failed: $($_.Exception.Message)) - retrying ..." -ForegroundColor Red
    }
    Start-Sleep -Seconds $RefreshSec
}
