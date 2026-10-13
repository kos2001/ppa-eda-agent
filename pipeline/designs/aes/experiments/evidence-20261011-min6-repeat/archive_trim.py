#!/usr/bin/env python3
"""Copy the audit files of finished aes diode-trim runs into this directory.

    python3 archive_trim.py <scratch aes dir> <tag> [<tag> ...]

Each run gets its final metrics, resolved config, flow provenance, antenna
summaries before and after the repair steps, the per-corner check reports, the
DiodeTrim report, the final netlist (gzip) and the violator attribution. A
sources.json records the sha256 and the scratch path of every copied file.
"""
import glob, gzip, hashlib, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import violator_sinks as vs  # noqa: E402


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def archive(aes, tag):
    run = f"{aes}/runs/{tag}"
    out = f"{HERE}/{tag}"
    os.makedirs(out, exist_ok=True)
    last = sorted(glob.glob(run + "/[0-9]*-openroad-checkantennas*"), key=lambda d: int(d.split("/")[-1].split("-")[0]))
    wanted = {
        "metrics.json": run + "/final/metrics.json",
        "resolved.json": run + "/resolved.json",
        "custom_flow_provenance.json": run + "/custom_flow_provenance.json",
        "antenna_summary-after-global-route.rpt": last[0] + "/reports/antenna_summary.rpt",
        "antenna_summary-final.rpt": last[-1] + "/reports/antenna_summary.rpt",
        "diode_trim.json": glob.glob(run + "/*-odb-diodetrim/diode_trim.json")[0],
    }
    for rpt in sorted(glob.glob(run + "/*-openroad-stapostpnr/*/checks.rpt")):
        wanted[rpt.split("/")[-2] + "-checks.rpt"] = rpt
    sources = {}
    for name, src in wanted.items():
        shutil.copyfile(src, f"{out}/{name}")
        sources[name] = {"sha256": sha(src), "source": "<scratch>/" + os.path.relpath(src, aes)}
    nl = run + "/final/nl/aes_cipher_top.nl.v"
    with open(nl, "rb") as f, gzip.GzipFile(f"{out}/aes_cipher_top.nl.v.gz", "wb", mtime=0) as g:
        g.write(f.read())
    sources["aes_cipher_top.nl.v.gz"] = {"sha256": sha(nl), "source": "<scratch>/" + os.path.relpath(nl, aes), "note": "sha256 of the uncompressed netlist"}
    insts = vs.netlist(f"{out}/aes_cipher_top.nl.v.gz")
    rows = []
    for rpt in sorted(glob.glob(f"{out}/*-checks.rpt")):
        for r in vs.attribute(insts, vs.violations(rpt)):
            rows.append({"corner": os.path.basename(rpt)[:-len("-checks.rpt")], **r})
    json.dump(rows, open(f"{out}/violator_sinks.json", "w"), indent=1)
    json.dump(sources, open(f"{out}/sources.json", "w"), indent=1)


if __name__ == "__main__":
    for t in sys.argv[2:]:
        archive(sys.argv[1], t)
