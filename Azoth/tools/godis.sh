#!/bin/bash
# usage: godis.sh BIN SYMS START STOP
objdump -d --no-show-raw-insn --start-address=$3 --stop-address=$4 "$1" 2>/dev/null | python3 -I -c '
import sys,re
syms=dict(l.rstrip("\n").split("\t") for l in open(sys.argv[1]))
for l in sys.stdin:
    m=re.search(r"\b(bl|b)\s+0x([0-9a-f]+)",l)
    if m and m.group(2) in syms: l=l.rstrip("\n")+"   ; "+syms[m.group(2)]+"\n"
    sys.stdout.write(l)' "$2"
