#!/bin/bash
# Basket timing of master-dev-certification-3 (B) against master-dev (A) on rizen
C=~/cert3
REF=$C/faust-ref/build/bin/faust
NEW=$C/faust-new/build/bin/faust
LOG=$C/campaign-fm.log
exec >> $LOG 2>&1
echo "=== campaign (A: -O3 -ffast-math, B: -O3) start $(date)"
for n in ref new; do
  until grep -q "^EXIT" $C/build-$n.log; do sleep 20; done
  grep "^EXIT" $C/build-$n.log
done
r=$($REF --version | sed -n 's/Source commit: //p'); n=$($NEW --version | sed -n 's/Source commit: //p')
echo "A (reference) $REF : $r"; echo "B (measured)  $NEW : $n"
if [ "$r" != "523288afc" ] || [ "$n" != "4ca9b5278" ]; then echo "WRONG BINARIES, abort"; echo FIN > $C/campaign-fm.FIN; exit 1; fi
export FAUST_LIB_PATH=$C/faust-ref/libraries
export CPT_BASE_FAUST=$REF CPT_JOBS=8 CPT_BASE_CXXOPTS="-O3 -ffast-math"
uname -a
for judge in clang21:/usr/bin/clang++ clang22:$HOME/opt/llvm-22/bin/clang++; do
  jn=${judge%%:*}; cxx=${judge#*:}
  echo "--- judge $jn: $($cxx --version | head -1)"
  for lang in cpp ocpp; do
    out=$C/runs-fm/$jn-$lang; mkdir -p $out; cp $C/cpu_arch.cpp $out/
    echo "--- $jn $lang start $(date +%H:%M:%S)"
    echo "command: CPT_LANG=$lang CPT_BASE_FAUST=$REF CPT_BASE_CXXOPTS=\"-O3 -ffast-math\" taskset -c 0-3,12-15 python3 cptime.py entries.txt $NEW '' $cxx -O3 $C/faust-ref $out 7"
    CPT_LANG=$lang taskset -c 0-3,12-15 timeout 5400 python3 $C/cptime.py $C/entries.txt $NEW "" $cxx "-O3" $C/faust-ref $out 7
    echo "--- $jn $lang end $(date +%H:%M:%S) exit $?"
  done
done
echo "=== campaign end $(date)"
echo FIN > $C/campaign-fm.FIN
