#!/usr/bin/env python3
"""Tables from first_iteration_counts.json (per net, diodes inserted in the first
repair iteration at each GRT_ANTENNA_MARGIN, same pre-repair database).

    python3 summarize_probe.py first_iteration_counts.json

The counts come from repair_probe.tcl: OpenLane's antenna_repair.tcl with the
step's own environment, one iteration, and `set_debug_level GRT repair_antennas 2`,
which makes RepairAntennas print "antenna <net> insert <N> diodes". The
pre-repair database (step 38 of the aes ITERS 5 / MARGIN 65 run) is not archived
(too large), so only this table step reruns from the repository.
"""
import collections, json, sys

d = {int(k): v for k, v in json.load(open(sys.argv[1])).items()}
margins = sorted(d)
real = d[0]  # margin 0 is the real rule: the nets that violate it, and the diodes it needs
print(f"{'margin':>6} {'nets':>5} {'diodes':>6} | nets with 1 / 2-9 / 10 / 11 / 12 / 13+ diodes | at cap (>=10)")
for m in margins:
    c, h = d[m], collections.Counter(d[m].values())
    print(f"{m:6d} {len(c):5d} {sum(c.values()):6d} | {h.get(1,0):4d} {sum(v for k,v in h.items() if 2<=k<=9):4d} "
          f"{h.get(10,0):3d} {h.get(11,0):3d} {h.get(12,0):3d} {sum(v for k,v in h.items() if k>=13):3d} | "
          f"{sum(v for k,v in h.items() if k>=10)}")
first = {}
for m in margins:
    for n, v in d[m].items():
        if v >= 10 and n not in first:
            first[n] = m
print("\nlowest probed margin at which a net reaches the cap:", dict(sorted(collections.Counter(first.values()).items())))
print("nets that stay at >=10 at every higher probed margin:",
      sum(1 for n, m in first.items() if all(d[mm].get(n, 0) >= 10 for mm in margins if mm >= m)), "of", len(first))
print(f"\n{'margin':>6} {'diodes':>7} | on capped nets: {'diodes':>6} {'share':>6} {'nets':>5} | capped nets violating the real rule | diodes the real rule needs there")
for m in margins:
    c = d[m]; tot = sum(c.values()); cap = {n for n, v in c.items() if v >= 10}
    on = sum(c[n] for n in cap); rv = cap & set(real)
    if cap:
        print(f"{m:6d} {tot:7d} |                 {on:6d} {100*on/tot:5.1f}% {len(cap):5d} | {len(rv):35d} | {sum(real[n] for n in rv)}")
print("\nreal-rule violators (margin 0):", len(real), "| diodes each needs:", dict(sorted(collections.Counter(real.values()).items())))
for m in margins[1:]:
    print(f"margin {m:2d}: real-rule violators needing >=10 diodes: {sum(1 for n in real if d[m].get(n, 0) >= 10)}")
