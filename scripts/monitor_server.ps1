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
# Free disk on the data partition (guardrail: a 100% run must not fill it) and the
# git commit the server is running (reproducibility, per CLAUDE.md).
df -PBG "$REPO" 2>/dev/null | awk 'NR==2{gsub(/G/,"",$4); gsub(/%/,"",$5); printf "DISK_AVAIL\t%s\nDISK_PCT\t%s\n", $4, $5}'
printf 'GIT\t%s\n' "$(git -C "$REPO" log -1 --format='%h %s' 2>/dev/null | cut -c1-58)"
# JVM heap during MATSim: resident set of all java processes (GB) + configured
# -Xmx. RSS is version-independent and always available; the MATSim memory log
# line (true heap used/total) is added below when present.
JRSS=$(ps -C java -o rss= 2>/dev/null | awk '{s+=$1} END{if(s>0) printf "%.1f", s/1048576}')
[ -n "$JRSS" ] && printf 'JAVA_RSS\t%s\n' "$JRSS"
JXMX=$(ps -C java -o args= 2>/dev/null | grep -oE 'Xmx[0-9]+[gGmM]' | head -1)
[ -n "$JXMX" ] && printf 'JAVA_XMX\t%s\n' "$JXMX"
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
  # Count only genuinely fatal markers. NOT a bare "ERROR": MATSim logs benign
  # network notices at ERROR level (e.g. "PreProcessDijkstra ... are dead ends!"),
  # which are not failures and would otherwise raise a false alarm.
  printf 'ERRORS\t%s\n' "$(grep -acE 'Traceback|RuntimeError|PipelineError|Exception in thread|Java return code|FATAL' "$LOG")"
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
  # MATSim's periodic memory log line (true JVM heap used/total), if present.
  printf 'JAVA_HEAP\t%s\n' "$(grep -aioE 'used ram: [0-9]+ mb|memory usage[^|]*|usedmemory[^,]*' "$LOG" | tail -1 | cut -c1-60)"
  # MATSim convergence, read straight from the controler log listeners (robust:
  # no dependency on the stats-file path or its .csv/.txt extension/delimiter).
  # ScoreStatsControlerListener logs "executed plan of each agent: <score>" once
  # per iteration; the last two give the current score and its delta.
  printf 'SCORE_EXEC\t%s\n'      "$(grep -aoE 'executed plan of each agent: -?[0-9.]+' "$LOG" | tail -1 | grep -oE '\-?[0-9.]+')"
  printf 'SCORE_EXEC_PREV\t%s\n' "$(grep -aoE 'executed plan of each agent: -?[0-9.]+' "$LOG" | tail -2 | head -1 | grep -oE '\-?[0-9.]+')"
  # ModeStatsControlerListener logs "mode share of mode <m> = <frac>"; keep the
  # last value seen per mode (final iteration) in first-seen order.
  printf 'MODES\t%s\n' "$(grep -aoE 'mode share of mode [a-z_]+ = [0-9.]+' "$LOG" | sed -E 's/mode share of mode //; s/ = /=/' | awk -F= '{v[$1]=$2; if(!($1 in seen)){ord[++n]=$1; seen[$1]=1}} END{for(i=1;i<=n;i++) printf "%s=%s,", ord[i], v[ord[i]]}' | sed 's/,$//')"
  # CPU sparkline: last ~24 cpu_pct samples from the load sampler CSV that
  # run_pipeline.sh writes next to the log (ts,cpu_pct,mem_used_gb,nproc).
  SAMP="${LOG%.log}_samples.csv"
  if [ -f "$SAMP" ]; then
    printf 'SPARK\t%s\n' "$(tail -n 24 "$SAMP" | awk -F, '$2 ~ /^[0-9.]+$/{printf "%s,", $2}' | sed 's/,$//')"
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

function ConvertTo-Double($s) {
    # Parse with the invariant culture: the server emits "." as the decimal point
    # (Java/MATSim logs, /proc), but a German-locale PowerShell would otherwise read
    # "-17.186" as thousands-grouped and produce nonsense. Returns $null on failure.
    $v = 0.0
    if ($s -and [double]::TryParse([string]$s, [Globalization.NumberStyles]::Float,
            [Globalization.CultureInfo]::InvariantCulture, [ref]$v)) {
        return $v
    }
    return $null
}

function New-Sparkline([string]$csv) {
    # Map a comma-separated list of 0..100 values to a unicode block sparkline.
    if (-not $csv) { return "" }
    $blocks = ' .:-=+*#%@'.ToCharArray()  # 10 ASCII levels (terminal-safe)
    $sb = ""
    foreach ($tok in $csv.Split(',')) {
        $v = ConvertTo-Double $tok
        if ($null -ne $v) {
            $idx = [int][math]::Round([math]::Min([math]::Max($v, 0), 100) / 100.0 * ($blocks.Count - 1))
            $sb += $blocks[$idx]
        }
    }
    return $sb
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
    # CPU history sparkline (last ~24 samples from the load sampler).
    if ($f['SPARK']) {
        Write-Host ("  trend   : {0}  (cpu %, last {1} samples)" -f (New-Sparkline $f['SPARK']), $f['SPARK'].Split(',').Count) -ForegroundColor DarkCyan
    }
    # RAM bar.
    if ($f['MEMPCT']) {
        $mempct = [int]$f['MEMPCT']
        Write-Host ("  mem     : [{0}] {1,3}%  {2} / {3} GB" -f (New-Bar ($mempct / 100.0) 40), $mempct, $f['MEMUSED'], $f['MEMTOTAL'])
    }
    Write-Host ("  load    : {0}   |   python {1}  java {2}   |   hottest {3}" -f `
        $f['LOAD'], $f['PYPROC'], $f['JAVAPROC'], $f['TOPPROC'])
    # JVM heap (shown whenever java is running, i.e. during MATSim).
    if ($f['JAVA_RSS']) {
        $jline = "  java    : heap RSS $($f['JAVA_RSS']) GB"
        if ($f['JAVA_XMX']) { $jline += " / -$($f['JAVA_XMX'])" }
        if ($f['JAVA_HEAP']) { $jline += "   ($($f['JAVA_HEAP']))" }
        Write-Host $jline -ForegroundColor Magenta
    }
    $diskText = ""
    if ($f['DISK_AVAIL']) { $diskText = "disk $($f['DISK_AVAIL']) GB free ($($f['DISK_PCT'])% used)" }
    if ($f['CACHE']) { $diskText = if ($diskText) { "$diskText   |   cache $($f['CACHE'])" } else { "cache $($f['CACHE'])" } }
    if ($diskText) {
        $diskColor = if ($f['DISK_PCT'] -and [int]$f['DISK_PCT'] -ge 90) { 'Red' } else { 'Gray' }
        Write-Host "  storage : $diskText" -ForegroundColor $diskColor
    }
    if ($f['GIT']) { Write-Host "  commit  : $($f['GIT'])" -ForegroundColor DarkGray }
    Write-Host "  log     : $($f['LOG'])" -ForegroundColor DarkGray
    Write-Host "  updated : $($f['MTIME'])   (server now $($f['NOW']))" -ForegroundColor DarkGray
    Write-Host ("  " + ("-" * $width))

    # --- phase: MATSim if iterations have started, else synpp ----------------
    $iter = $f['ITER']
    if ($null -ne $iter -and $iter -ne '') {
        $i = [int]$iter
        $total = if ($f['ITER_TOTAL'] -and [int]$f['ITER_TOTAL'] -gt 0) { [int]$f['ITER_TOTAL'] } else { [math]::Max($LastIteration, 1) }
        $frac = if ($total -gt 0) { [math]::Min($i / $total, 1.0) } else { 0 }
        Write-Host "  phase   : MATSim simulation" -ForegroundColor Yellow
        Write-Host ("  iter    : [{0}] {1} / {2}  ({3:P0})" -f (New-Bar $frac 40), $i, $total, $frac)
        if ($f['PERITER']) {
            $per = [int]$f['PERITER']
            $remain = [math]::Max($total - $i, 0)
            $eta = $per * $remain
            $finishText = ""
            $nowParsed = [datetime]::MinValue
            if ([datetime]::TryParse($f['NOW'], [ref]$nowParsed)) {
                $finishText = "   finish ~" + $nowParsed.AddSeconds($eta).ToString("HH:mm")
            }
            Write-Host ("  pace    : {0}s / iteration   ETA ~{1}{2}  ({3} iters left)" -f $per, (Format-Seconds $eta), $finishText, $remain) -ForegroundColor Gray
        }
        # Convergence: avg executed score (+delta) and modal split, parsed from the
        # controler log listeners.
        $score = ConvertTo-Double $f['SCORE_EXEC']
        if ($null -ne $score) {
            $deltaText = ""
            $prev = ConvertTo-Double $f['SCORE_EXEC_PREV']
            if ($null -ne $prev) {
                $deltaText = "  (delta {0:+0.000;-0.000;0.000})" -f ($score - $prev)
            }
            Write-Host ("  score   : {0:0.000}{1}  (avg executed)" -f $score, $deltaText) -ForegroundColor Cyan
        }
        if ($f['MODES']) {
            $modeMap = @{}
            $order = @()
            foreach ($pair in $f['MODES'].Split(',')) {
                $kv = $pair.Split('=')
                if ($kv.Count -eq 2) {
                    $mv = ConvertTo-Double $kv[1]
                    if ($null -ne $mv) { $modeMap[$kv[0]] = $mv; $order += $kv[0] }
                }
            }
            # "outside" is the cordon out-of-scope pseudo-mode (the part of a trip
            # beyond the cordon), not a real transport mode. Show the real modes
            # renormalised to 100% (the modal split inside the study area), and
            # report outside separately as its own share of all trips.
            $realSum = 0.0
            foreach ($m in $order) { if ($m -ne 'outside') { $realSum += $modeMap[$m] } }
            if ($realSum -le 0) { $realSum = 1.0 }
            $parts = @()
            foreach ($m in $order) {
                if ($m -ne 'outside') { $parts += ("{0} {1:P0}" -f $m, ($modeMap[$m] / $realSum)) }
            }
            if ($parts.Count -gt 0) {
                Write-Host ("  modes   : " + ($parts -join "  ") + "   (=100% inside)") -ForegroundColor Cyan
            }
            if ($modeMap.ContainsKey('outside')) {
                Write-Host ("  outside : {0:P1} of all trips (cross-cordon, out of scope)" -f $modeMap['outside']) -ForegroundColor DarkGray
            }
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
$wasAlive = $false   # only beep on a real running -> ended transition
$beeped = $false
while ($true) {
    try {
        $snap = Get-Snapshot
        Show-Dashboard $snap
        $f = $snap.Fields
        $done = ($f['DONE'] -and [int]$f['DONE'] -gt 0)
        $ended = ($f['ALIVE'] -ne 'yes')
        if (-not $beeped -and ($done -or ($ended -and $wasAlive))) {
            $errN = if ($f['ERRORS']) { [int]$f['ERRORS'] } else { 0 }
            if ($done -and $errN -eq 0) {
                Write-Host "  >>> RUN FINISHED <<<" -ForegroundColor Green
                try { [console]::Beep(880, 200); [console]::Beep(1175, 350) } catch {}
            }
            else {
                Write-Host "  >>> RUN ENDED - check errors ($errN) <<<" -ForegroundColor Red
                try { [console]::Beep(440, 250); [console]::Beep(330, 450) } catch {}
            }
            $beeped = $true
        }
        if ($f['ALIVE'] -eq 'yes') { $wasAlive = $true }
    }
    catch {
        Write-Host "  (poll failed: $($_.Exception.Message)) - retrying ..." -ForegroundColor Red
    }
    Start-Sleep -Seconds $RefreshSec
}
