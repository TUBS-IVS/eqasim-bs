#!/usr/bin/env bash
# Read-only analysis of the "Failed loading line numbers" traces in the logs of the 100 % production run
# prod100_20261031 and the September run prod100_20260925 on felix (C5f). Starts nothing, writes nothing.
# Run as: ssh felix 'bash -s' < felix_log_analysis.sh
export LC_ALL=C
L1=$HOME/prod100_20261031/logs/run_20261031T071313.log   # launch 1
L2=$HOME/prod100_20261031/logs/run_20261101T050111.log   # launch 2 (ends in NUL bytes after the VM crash: grep -a)
S=$HOME/prod100_20260925/logs/B_matsim.log               # September 100 % run, MATSim log
J=$HOME/prod100_20261031/cache/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar

echo "== 1. phrases per log (grep -acF, exact phrase)"
for f in "$L1" "$L2" "$S"; do
  echo "-- $f"
  for p in "Unsupported class file major version 69" "Failed loading line numbers" "ASM is probably out of date"; do
    printf '   %s: ' "$p"; grep -acF "$p" "$f"
  done
done

echo "== 2. shape of the traces by entry point (the last main() frame of each trace)"
for f in "$L1" "$S"; do
  echo "-- $f"
  awk '
  /^WARNING: Failed loading line numbers/ { n++; intrace=1; mainfr=""; frames=0; total+=2; next }
  intrace && /^[ \t]+at / { frames++; total++; if ($0 ~ /\.main\(/) mainfr=$0; next }
  intrace && /^java\.lang\.IllegalArgumentException/ { total++; next }
  intrace { intrace=0; c[mainfr]++; fr[frames]++ }
  END { printf "   traces=%d, trace lines in total (2 header lines + exception + frames)=%d\n", n, total
        for (k in c) printf "   %5d  %s\n", c[k], k
        for (k in fr) printf "   %s frames: %d traces\n", k, fr[k] }
  ' "$f" | cut -c1-200
done

echo "== 3. JVM launches in launch 1 that name the PT batch router (lines outside traces)"
printf '   '; grep -a "RunBatchPublicTransportRouter" "$L1" | grep -avc '^[[:space:]]*at '

echo "== 4. the first trace of launch 1 in full"
n=$(grep -anF "Unsupported class file major version 69" "$L1" | head -1 | cut -d: -f1)
echo "   first hit at line $n"
sed -n "$((n-8)),$((n+42))p" "$L1" | tr -d '\r' | cut -c1-240

echo "== 5. the production jar: class file version of eqasim and MATSim classes (bytes 4-7 of the header: minor major)"
unzip -p "$J" org/eqasim/braunschweig/RunSimulation.class | head -c 8 | od -An -tx1
unzip -p "$J" org/matsim/core/controler/Injector.class | head -c 8 | od -An -tx1
echo "== 6. the production jar: ASM inside it"
echo "   entries below com/google/inject/internal/asm/: $(unzip -l "$J" | grep -c 'com/google/inject/internal/asm/')"
echo "   plain org/objectweb/asm classes: $(unzip -l "$J" | grep -c ' org/objectweb/asm/')"
unzip -p "$J" 'com/google/inject/internal/asm/$Opcodes.class' | python3 -c "import sys,re; d=sys.stdin.buffer.read(); v=sorted({int(x) for x in re.findall(rb'V(\d+)(?![\d_])', d)}); print('   highest Java version constant of the embedded ASM Opcodes: V%d' % v[-1])"
unzip -p "$J" 'com/google/inject/internal/util/LineNumbers.class' | python3 -c "import sys; d=sys.stdin.buffer.read(); print('   LineNumbers carries the warning text: %d' % d.count(b'ASM is probably out of date'))"
ls -la "$J"
