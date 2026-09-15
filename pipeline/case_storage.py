"""Compact JSON case storage, retaining the existing schema and file paths.

Existing files lose only whitespace outside JSON strings. Numeric spellings,
escaped strings, and every recorded measurement remain byte-for-byte intact.
Run without --write to estimate savings; --write replaces each file atomically.
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

CASES = Path(__file__).resolve().parents[1] / "reference-db/cases"
_STRINGS_OR_SPACE = re.compile(r'"(?:\\.|[^"\\])*"|(\s+)')


def compact_text(text: str) -> str:
    json.loads(text)  # Reject malformed evidence before attempting a rewrite.
    return _STRINGS_OR_SPACE.sub(lambda m: "" if m.group(1) else m.group(0), text) + "\n"


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_case_json(path: Path, case: dict) -> None:
    atomic_write(path, json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")


def compact_cases(directory: Path, write: bool = False) -> dict:
    rows = []
    for path in sorted(directory.glob("*.json")):
        original = path.read_bytes()
        compact = compact_text(original.decode("utf-8"))
        encoded = compact.encode("utf-8")
        if write and len(encoded) < len(original):
            # Avoid overwriting a concurrent review or new measurement.
            if path.read_bytes() != original:
                raise RuntimeError(f"Case changed during compaction: {path}")
            atomic_write(path, compact)
            if path.read_bytes() != encoded:
                raise RuntimeError(f"Case verification failed: {path}")
        rows.append({"file": path.name, "before_bytes": len(original),
                     "after_bytes": min(len(encoded), len(original))})
    before = sum(row["before_bytes"] for row in rows)
    after = sum(row["after_bytes"] for row in rows)
    return {"write": write, "files": len(rows), "before_bytes": before,
            "after_bytes": after, "saved_bytes": before - after,
            "saved_percent": round(100 * (before - after) / before, 2) if before else 0,
            "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.cases.is_dir():
        parser.error(f"case directory not found: {args.cases}")
    print(json.dumps(compact_cases(args.cases, args.write), indent=2))


if __name__ == "__main__":
    main()
