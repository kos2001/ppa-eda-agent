#!/usr/bin/env python3
"""Install official Volare release assets without GitHub's rate-limited API."""
from __future__ import annotations

import argparse
import tarfile
import urllib.request
from pathlib import Path

import zstandard

REPOSITORY = "https://github.com/chipfoundry/volare/releases/download"


def asset_url(version: str, asset: str) -> str:
    return f"{REPOSITORY}/sky130-{version}/{asset}.tar.zst"


def members(version: str, asset: str, limit: int = 20) -> list[str]:
    with urllib.request.urlopen(asset_url(version, asset)) as response:
        reader = zstandard.ZstdDecompressor().stream_reader(response)
        archive = tarfile.open(fileobj=reader, mode="r|")
        names = []
        for member in archive:
            names.append(member.name)
            if len(names) >= limit:
                break
        return names


def install(version: str, assets: list[str], root: Path) -> None:
    destination = root / "volare" / "sky130" / "versions" / version
    destination.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        print(f"installing {asset} into {destination}", flush=True)
        with urllib.request.urlopen(asset_url(version, asset)) as response:
            reader = zstandard.ZstdDecompressor().stream_reader(response)
            archive = tarfile.open(fileobj=reader, mode="r|")
            for member in archive:
                target = (destination / member.name).resolve()
                if destination.resolve() not in (target, *target.parents):
                    raise ValueError(f"unsafe archive member: {member.name}")
                archive.extract(member, destination, filter="data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--root", type=Path, default=Path("pdk"))
    parser.add_argument("--assets", nargs="+", default=["common", "sky130_fd_sc_hd"])
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for asset in args.assets:
            print(asset)
            print("\n".join(members(args.version, asset)))
        return
    install(args.version, args.assets, args.root)


if __name__ == "__main__":
    main()
