#!/bin/sh
for M in "$@"; do
  docker run --rm -e _TCL_ENV_IN=/env.tcl -v <scratch>/aes-exp/aes:/design:ro -v /Volumes/T9-Workspace/gitspace/ppa-eda-agent/pdk:/pdk:ro -v <scratch>/probe/env_$M.tcl:/env.tcl:ro -v <scratch>/probe/rep.tcl:/rep.tcl:ro ghcr.io/efabless/openlane2:2.3.10 sh -c 'openroad -exit -no_splash /rep.tcl' > <scratch>/probe/out_$M.txt 2>&1
  echo "margin $M done: $(grep -E 'GRT-0012' <scratch>/probe/out_$M.txt | head -1) $(grep -E 'GRT-0015' <scratch>/probe/out_$M.txt | head -1)"
done
