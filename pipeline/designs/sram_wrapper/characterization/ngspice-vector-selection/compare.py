"""Compare retained vectors, transient measurements and RSS on one circuit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--simulator", type=Path, required=True)
ap.add_argument("--output-dir", type=Path, required=True)
args = ap.parse_args()
root = args.output_dir.resolve()
root.mkdir(parents=True, exist_ok=False)
startup = root / "startup"
startup.mkdir()
(startup / "spinit").write_text("set numdgt=15\n")
env = dict(os.environ, SPICE_SCRIPTS=str(startup), NGSPICE_MEAS_PRECISION="15")
source = """Retention comparison; same nonlinear circuit and solver
vvdd vdd 0 1.8
vclk clk 0 PULSE(0 1.8 1n .2n .2n 4n 10n)
rdrive clk out 1k
rsupply vdd out 10k
cout out 0 .5p
rdiod out db 100k
dout db 0 dtest
.model dtest d is=1e-15
xcell out q cell
.subckt cell a q
r1 a internal 1k
c1 internal 0 .1p
r2 internal q 1
.ends cell
.options KLU method=gear reltol=.001 numdgt=15
.tran 10p 30n 0 10p UIC
.meas tran delay TRIG v(clk) VAL=.9 RISE=1 TARG v(out) VAL=.9 RISE=1
.meas tran slew TRIG v(out) VAL=.18 RISE=1 TARG v(out) VAL=1.62 RISE=1
.meas tran stored FIND v(xcell.internal) AT=4n
.meas tran diff FIND par('(v(out,q))') AT=4n
.meas tran power AVG par('(-1*v(vdd)*I(vvdd))') FROM=0 TO=30n
.meas tran wave AVG par('(v(out)*v(out))') FROM=0 TO=30n
"""
# Unobserved loads add retained data without changing any measured connection.
source += "".join(f"r{i} clk unused{i} 10k\nc{i} unused{i} 0 1p\n" for i in range(2000))
source += ".end\n"
results = {}
for kind in ("default", "selected"):
    deck = root / f"{kind}.sp"
    deck.write_text(source)
    if kind == "selected":
        # Three par() measurements expand to pa_00..02 in ngspice 46.
        vectors = ["v(clk)", "v(out)", "v(q)", "v(xcell.internal)",
                   "v(vdd)", "i(vvdd)", "v(pa_00)", "v(pa_01)", "v(pa_02)"]
        deck.write_text(source.replace(".end\n", ".save " + " ".join(vectors) + "\n.end\n"))
        without_save = "\n".join(l for l in deck.read_text().splitlines() if not l.startswith(".save")) + "\n"
        assert without_save == source, "selection changed non-save cards"
    result = subprocess.run(["/usr/bin/time", "-l", str(args.simulator.resolve()), "-b", str(deck)],
                            env=env, capture_output=True, text=True, timeout=60)
    (root / f"{kind}.log").write_text(result.stdout + result.stderr)
    names = ("delay", "slew", "stored", "diff", "power", "wave")
    measured = {m[1]: float(m[2]) for m in re.finditer(
        r"(?m)^([a-z]+)\s*=\s*([-+\d.eE]+)", result.stdout) if m[1] in names}
    assert result.returncode == 0 and set(measured) == set(names), (kind, result.returncode, measured)
    rss = re.search(r"(\d+)\s+maximum resident set size", result.stderr)
    results[kind] = {"measurements": measured, "maximum_rss_bytes": int(rss[1]) if rss else None,
                     "deck_sha256": hashlib.sha256(deck.read_bytes()).hexdigest()}
assert results["default"]["measurements"] == results["selected"]["measurements"], results
report = {"scope": "same nonlinear RC test circuit; not full SRAM functional/PVT qualification",
          "simulator": str(args.simulator.resolve()),
          "simulator_sha256": hashlib.sha256(args.simulator.read_bytes()).hexdigest(),
          "comparison_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "only_save_cards_added": True, "identical_reported_measurements": True,
          "results": results}
(root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
