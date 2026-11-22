#!/bin/bash
C=~/cert3; export FAUST_LIB_PATH=$C/faust-ref/libraries CPT_BASE_FAUST=$C/faust-ref/build/bin/faust CPT_BASE_CXXOPTS="-O3 -ffast-math" CPT_COPIES=3
exec >> $C/tp.log 2>&1
for judge in clang21:/usr/bin/clang++ clang22:$HOME/opt/llvm-22/bin/clang++; do jn=${judge%%:*}; cxx=${judge#*:}
  for lang in cpp ocpp; do out=$C/runs-tp/$jn-$lang; mkdir -p $out; cp $C/cpu_arch.cpp $out/
    echo "--- $jn $lang"; CPT_LANG=$lang taskset -c 0-3,12-15 timeout 1800 python3 $C/cptime.py $C/entries-tp.txt $C/faust-new/build/bin/faust "" $cxx "-O3" $C/faust-ref $out 9 2>&1 | grep -E "changed|ThinPlate|built"
  done; done
echo FIN > $C/tp.FIN
