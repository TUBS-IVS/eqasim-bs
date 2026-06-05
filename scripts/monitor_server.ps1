<#
.SYNOPSIS
    Live terminal dashboard for an eqasim-bs pipeline run on the Linux server.

.DESCRIPTION
    Polls the run server over SSH every few seconds and renders a refreshing
    status panel in a single SSH round-trip (nothing is installed on the server):

      - session alive (tmux), server load, RAM bar, live CPU-utilisation bar
        (busy cores out of nproc, sampled from /proc/stat)
      - synpp phase: stage X / N progress bar + short stage name + how long the
        current stage has been running + total run elapsed
      - MATSim phase: iteration X / last progress bar + seconds/iteration + ETA
      - working-cache size (grows as stages cache), worker process counts
        (python / java), top CPU process, error/warning tallies
      - tail of the live log

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
    config). Auto-detected from the log when possible; this is the fallback.
    Default: 99 (a 100-iteration run, 0..99).
.PARAMETER Once
    Print a single snapshot and exit (no live loop).

.EXAMPLE
    ./scripts/monitor_server.ps1
    ./scripts/monitor_server.ps1 -RefreshSec 5
    ./scripts/monitor_server.ps1 -LastIteration 10 -Once
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
NOW_E=$(date +%s)
printf 'NOW\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
printf 'LOAD\t%s\n' "$(uptime | sed 's/.*load average: //')"
printf 'NPROC\t%s\n' "$(nproc)"
# RAM in GB + percent.
free -m | awk '/Mem:/{printf "MEMUSED\t%d\nMEMTOTAL\t%d\nMEMPCT\t%d\n", $3/1024, $2/1024, ($3*100)/$2}'
# Live CPU utilisation: sample /proc/stat twice and derive the busy percentage
# (100 * (1 - delta_idle/delta_total)) across all cores.
read -r _ u1 n1 s1 i1 w1 q1 sq1 st1 _ < /proc/stat
sleep 0.35
read -r _ u2 n2 s2 i2 w2 q2 sq2 st2 _ < /proc/stat
t1=$((u1+n1+s1+i1+w1+q1+sq1+st1)); t2=$((u2+n2+s2+i2+w2+q2+sq2+st2))
dt=$((t2-t1)); di=$((i2-i1))
if [ "$dt" -gt 0 ]; then printf 'CPUPCT\t%d\n' $(( (100*(dt-di))/dt )); else printf 'CPUPCT\t0\n'; fi
# Worker processes + the single hottest process (shows where the CPU goes).
printf 'PYPROC\t%s\n' "$(pgrep -fc '[p]ython' 2>/dev/null || echo 0)"
printf 'JAVAPROC\t%s\n' "$(pgrep -fc '[j]ava' 2>/dev/null || echo 0)"
printf 'TOPPROC\t%s\n' "$(ps -eo pcpu,comm --sort=-pcpu 2>/dev/null | awk 'NR==2{printf "%s%% %s", $1, $2}')"
if [ -n "$LOG" ]; then
  printf 'MTIME\t%s\n' "$(date -r "$LOG" '+%Y-%m-%d %H:%M:%S')"
  # Total run elapsed (now - first timestamped log line).
  START=$(head -n 50 "$LOG" | grep -aoE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+' | head -1)
  if [ -n "$START" ]; then
    SE=$(date -d "${START/T/ }" +%s 2>/dev/null)
    if [ -n "$SE" ]; then EL=$((NOW_E-SE)); printf 'ELAPSED\t%02d:%02d:%02d\n' $((EL/3600)) $(((EL%3600)/60)) $((EL%60)); fi
  fi
  # synpp stage progress + short stage name + how long the current stage runs.
  printf 'PROGRESS\t%s\n' "$(grep -aoE 'Pipeline progress: [0-9]+/[0-9]+' "$LOG" | tail -1 | grep -oE '[0-9]+/[0-9]+')"
  STAGELINE=$(grep -aE 'Executing stage ' "$LOG" | tail -1)
  printf 'STAGE_NAME\t%s\n' "$(echo "$STAGELINE" | sed -E 's/.*Executing stage //; s/__[a-f0-9]+ .*//; s/ \.\.\..*//')"
  STAGE_TS=$(echo "$STAGELINE" | grep -aoE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+')
  if [ -n "$STAGE_TS" ]; then
    STE=$(date -d "${STAGE_TS/T/ }" +%s 2>/dev/null)
    if [ -n "$STE" ]; then SS=$((NOW_E-STE)); printf 'STAGE_SEC\t%s\n' "$SS"; fi
  fi
  # A human-readable detail line: the latest interesting diagnostic print().
  printf 'DETAIL\t%s\n' "$(grep -aE '^\[|Writing |Filtering |Sampling |Solving |Raking |chunk|converg|margin' "$LOG" | tail -1 | cut -c1-100)"
  # MATSim iteration + total (auto-detect lastIteration) + seconds/iteration.
  printf 'ITER\t%s\n' "$(grep -aoE 'ITERATION [0-9]+' "$LOG" | tail -1 | grep -oE '[0-9]+')"
  printf 'ITER_TOTAL\t%s\n' "$(grep -aoE 'lastIteration[^0-9]+[0-9]+' "$LOG" | tail -1 | grep -oE '[0-9]+$')"
  # Seconds for the last completed iteration (gap between the last two BEGINS).
  BEGINS=$(grep -aE 'ITERATION [0-9]+ BEGINS' "$LOG" | tail -2 | grep -aoE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+')
  if [ "$(echo "$BEGINS" | wc -l)" -ge 2 ]; then
    b1=$(date -d "$(echo "$BEGINS" | head -1 | tr T ' ')" +%s 2>/dev/null)
    b2=$(date -d "$(echo "$BEGINS" | tail -1 | tr T ' ')" +%s 2>/dev/null)
    if [ -n "$b1" ] && [ -n "$b2" ]; then printf 'PERITER\t%s\n' "$((b2-b1))"; fi
  fi
  printf 'ERRORS\t%s\n' "$(grep -acE 'Traceback|RuntimeError|PipelineError|Exception in thread|ERROR' "$LOG")"
  printf 'WARNINGS\t%s\n' "$(grep -acE ' WARN| WARNING' "$LOG")"
  printf 'DONE\t%s\n' "$(grep -acE 'Pipeline finished|MATSim run finished|run completed' "$LOG")"
  # Working-cache size (grows as synpp caches). Best-effort with a hard timeout so
  # a huge cache never stalls the dashboard. Parse the working dir from the log.
  WD=$(grep -aoE 'Working directory[^:]*: .*' "$LOG" | tail -1 | sed -E 's/.*: //; s/[[:space:]]*$//')
  WDP=""
  if [ -n "$WD" ]; then
    case "$WD" in /*) WDP="$WD";; *) WDP="$REPO/$WD";; esac
  fi
  # Fallback: if the working dir could not be parsed (or no longer exists), use
  # the most recently modified cache_* directory in the repo data tree.
  if [ -z "$WDP" ] || [ ! -d "$WDP" ]; then
    WDP=$(ls -dt "$REPO"/eqasim-data/cache_* 2>/dev/null | head -1)
  fi
  if [ -n "$WDP" ] && [ -d "$WDP" ]; then
    printf 'CACHE\t%s\n' "$(timeout 5 du -sh "$WDP" 2>/dev/null | cut -f1)"
  fi
  echo '---TAIL---'
  tail -n 10 "$LOG"
fi
'@
# The here-string above uses Windows line endings (CRLF). When the script is
# piped to the remote "bash", every line would keep a trailing carriage return,
# so e.g. REPO=~/eqasim-bs<CR> yields the invalid path "$REPO<CR>/logs" and all
# REPO-derived fields come back empty. Normalise to LF before sending.
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

function New-Bar([double]$frac, [int]$len = 40) {
    $frac = [math]::Min([math]::Max($frac, 0.0), 1.0)
    $fill = [int]([math]::Round($frac * $len))
    return ('#' * $fill) + ('.' * ($len - $fill))
}

function Format-Seconds([int]$s) {
    if ($s -lt 0) { return "?" }
    if ($s -ge 3600) { return ("{0:00}:{1:00}:{2:00}" -f [int]($s / 3600), [int](($s % 3600) / 60), ($s % 60)) }
    return ("{0:00}:{1:00}" -f [int]($s / 60), ($s % 60))
}

function Show-Dashboard($snap) {
    $f = $snap.Fields
    Clear-Host
    $alive = $f['ALIVE']
    $aliveColor = if ($alive -eq 'yes') { 'Green' } else { 'Red' }
    $width = 72
    Write-Host "  eqasim-bs run monitor  -  $server" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * $width))

    # --- session + machine ---------------------------------------------------
    $sessionText = if ($alive -eq 'yes') { 'RUNNING (tmux: eqasim)' } else { 'not running' }
    Write-Host -NoNewline "  session : "
    Write-Host -NoNewline $sessionText -ForegroundColor $aliveColor
    if ($f['ELAPSED']) { Write-Host "    elapsed $($f['ELAPSED'])" -ForegroundColor Gray } else { Write-Host "" }

    $nproc = if ($f['NPROC']) { [int]$f['NPROC'] } else { 1 }
    # Live CPU bar (busy cores out of nproc).
    if ($f['CPUPCT']) {
        $cpupct = [int]$f['CPUPCT']
        $busy = [math]::Round($cpupct / 100.0 * $nproc, 1)
        $cpuColor = if ($cpupct -ge 60) { 'Green' } elseif ($cpupct -ge 15) { 'Yellow' } else { 'DarkYellow' }
        Write-Host -NoNewline ("  cpu     : [{0}] " -f (New-Bar ($cpupct / 100.0) 40))
        Write-Host ("{0,3}%  ~{1}/{2} cores busy" -f $cpupct, $busy, $nproc) -ForegroundColor $cpuColor
    }
    # RAM bar.
    if ($f['MEMPCT']) {
        $mempct = [int]$f['MEMPCT']
        Write-Host ("  mem     : [{0}] {1,3}%  {2} / {3} GB" -f (New-Bar ($mempct / 100.0) 40), $mempct, $f['MEMUSED'], $f['MEMTOTAL'])
    }
    Write-Host ("  load    : {0}   |   python {1}  java {2}   |   hottest {3}" -f `
        $f['LOAD'], $f['PYPROC'], $f['JAVAPROC'], $f['TOPPROC'])
    if ($f['CACHE']) { Write-Host "  cache   : $($f['CACHE']) on disk" }
    Write-Host "  log     : $($f['LOG'])" -ForegroundColor DarkGray
    Write-Host "  updated : $($f['MTIME'])   (server now $($f['NOW']))" -ForegroundColor DarkGray
    Write-Host ("  " + ("-" * $width))

    # --- phase: MATSim if iterations have started, else synpp ----------------
    $iter = $f['ITER']
    if ($iter -ne $null -and $iter -ne '') {
        $i = [int]$iter
        $total = if ($f['ITER_TOTAL'] -and [int]$f['ITER_TOTAL'] -gt 0) { [int]$f['ITER_TOTAL'] } else { [math]::Max($LastIteration, 1) }
        $frac = if ($total -gt 0) { [math]::Min($i / $total, 1.0) } else { 0 }
        Write-Host "  phase   : MATSim simulation" -ForegroundColor Yellow
        Write-Host ("  iter    : [{0}] {1} / {2}  ({3:P0})" -f (New-Bar $frac 40), $i, $total, $frac)
        if ($f['PERITER']) {
            $per = [int]$f['PERITER']
            $remain = [math]::Max($total - $i, 0)
            $eta = $per * $remain
            Write-Host ("  pace    : {0}s / iteration   ETA ~{1}  ({2} iters left)" -f $per, (Format-Seconds $eta), $remain) -ForegroundColor Gray
        }
    }
    else {
        Write-Host "  phase   : population synthesis (synpp)" -ForegroundColor Yellow
        $progress = $f['PROGRESS']
        if ($progress -and $progress -match '^(\d+)/(\d+)$') {
            $pdone = [int]$matches[1]
            $ptotal = [math]::Max([int]$matches[2], 1)
            $pfrac = [math]::Min($pdone / $ptotal, 1.0)
            Write-Host ("  stages  : [{0}] {1} / {2}  ({3:P0})" -f (New-Bar $pfrac 40), $pdone, $ptotal, $pfrac)
        }
        $stageName = $f['STAGE_NAME']
        $stageAge = if ($f['STAGE_SEC']) { "  (running " + (Format-Seconds ([int]$f['STAGE_SEC'])) + ")" } else { "" }
        if ($stageName) { Write-Host ("  current : {0}{1}" -f $stageName, $stageAge) -ForegroundColor White }
        if ($f['DETAIL']) { Write-Host "  detail  : $($f['DETAIL'])" -ForegroundColor Gray }
    }

    # --- tallies + completion ------------------------------------------------
    $errN = if ($f['ERRORS']) { [int]$f['ERRORS'] } else { 0 }
    $warnN = if ($f['WARNINGS']) { [int]$f['WARNINGS'] } else { 0 }
    $errColor = if ($errN -gt 0) { 'Red' } else { 'DarkGray' }
    Write-Host -NoNewline "  health  : "
    Write-Host -NoNewline ("errors {0}" -f $errN) -ForegroundColor $errColor
    Write-Host ("   warnings {0}" -f $warnN) -ForegroundColor DarkGray
    if ($f['DONE'] -and [int]$f['DONE'] -gt 0) {
        Write-Host "  status  : FINISHED" -ForegroundColor Green
    }
    Write-Host ("  " + ("-" * $width))

    # --- live log tail -------------------------------------------------------
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
