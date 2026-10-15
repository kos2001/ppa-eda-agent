"""Retain exact-run geometry and compact reports, following the AES evidence archive.

Usage: python3 archive.py <completed-run-directory>
Artifact presence is not a signoff verdict. Never overwrite an earlier archive.
"""
import gzip
import hashlib
import json
from pathlib import Path
import sys


def archive(run):
    run = Path(run).resolve()
    required = [run / "final/metrics.json", run / "resolved.json"]
    for pattern in ("final/def/*.def", "final/nl/*.nl.v"):
        matches = list(run.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f"expected one {pattern} in completed run")
        required.extend(matches)
    if not all(p.is_file() for p in required):
        raise ValueError("final metrics/configuration missing")
    optional_patterns = (
        "custom_flow_provenance.json", "*equivalence*.log",
        "*openroad-stapostpnr/*/checks.rpt",
        "*openroad-stapostpnr/*/ws.*.rpt",
        "*openroad-stapostpnr/*/max.rpt",
        "*openroad-checkantennas*/reports/antenna_summary.rpt",
        "*openroad-repairantennas*/*/reports/antenna_summary.rpt",
        "*odb-fanoutbuffers*/fanout_repair.json",
        "*magic-drc/reports/drc_violations.magic.rpt",
        "*netgen-lvs/reports/lvs.netgen.json",
    )
    wanted = set(required)
    missing_patterns = []
    for pattern in optional_patterns:
        matches = list(run.glob(pattern))
        wanted.update(matches)
        if not matches:
            missing_patterns.append(pattern)
    out = Path(__file__).resolve().parent / run.name
    out.mkdir(exist_ok=False)
    sources = {}
    for source in sorted(wanted):
        relative = source.relative_to(run)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        compressed = len(data) >= 65536
        target = out / (str(relative) + (".gz" if compressed else ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(data, mtime=0) if compressed else data)
        restored = gzip.decompress(target.read_bytes()) if compressed else target.read_bytes()
        if hashlib.sha256(restored).hexdigest() != digest or source.read_bytes() != data:
            raise ValueError(f"archive/source changed during copy: {source}")
        sources[target.relative_to(out).as_posix()] = {
            "source": relative.as_posix(), "uncompressed_sha256": digest,
            "uncompressed_bytes": len(data), "gzip": compressed,
        }
    manifest = {"run": run.name, "scope": "exact-run artifacts, not a verdict",
                "missing_optional_patterns": missing_patterns, "sources": sources}
    (out / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Archived and byte-verified {len(sources)} files: {out}")


if __name__ == "__main__":
    archive(sys.argv[1])
