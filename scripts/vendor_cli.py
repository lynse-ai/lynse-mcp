#!/usr/bin/env python3
"""Prepare and verify the CLI wheel from the exact revision pinned in requirements.txt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

PIN = re.compile(r"^lynse-cli @ git\+https://github\.com/lynse-ai/lynse-cli\.git@([0-9a-f]{40})$", re.M)


def pinned_revision(requirements: Path) -> str:
    matches = PIN.findall(requirements.read_text())
    if len(matches) != 1:
        raise ValueError("Expected exactly one immutable lynse-cli Git revision in requirements.txt")
    return matches[0]


def prepare(requirements: Path, repo: Path, vendor: Path) -> None:
    revision = pinned_revision(requirements)
    vendor.mkdir(parents=True, exist_ok=True)
    if list(vendor.iterdir()):
        raise ValueError("Use an empty vendor directory so stale wheels cannot be included")
    with tempfile.TemporaryDirectory(prefix="lynse-cli-wheel-") as temp:
        root = Path(temp)
        archive = root / "source.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), revision], check=True)
        source = root / "source"
        source.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(source, filter="data")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                        "--wheel-dir", str(vendor.resolve()), str(source)], check=True)
    wheels = list(vendor.glob("lynse_cli-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one built lynse-cli wheel")
    wheel = wheels[0]
    (vendor / "lynse-cli-source.json").write_text(json.dumps({
        "revision": revision, "wheel": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }, indent=2) + "\n")


def use_wheel(requirements: Path, vendor: Path) -> None:
    revision = pinned_revision(requirements)
    manifest = json.loads((vendor / "lynse-cli-source.json").read_text())
    if manifest.get("revision") != revision:
        raise ValueError("Vendored CLI revision does not match requirements.txt")
    name = manifest.get("wheel", "")
    if not name or Path(name).name != name:
        raise ValueError("Invalid CLI wheel filename")
    wheel = vendor / name
    if list(vendor.glob("lynse_cli-*.whl")) != [wheel]:
        raise ValueError("Expected exactly the CLI wheel recorded in the manifest")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest.get("sha256"):
        raise ValueError("Vendored CLI wheel checksum does not match the manifest")
    location = str(wheel) if wheel.is_absolute() else "./" + str(wheel)
    requirements.write_text(PIN.sub(lambda _: location, requirements.read_text()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "use"))
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    parser.add_argument("--vendor", type=Path, default=Path("vendor"))
    parser.add_argument("--repo", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.repo is None:
            parser.error("prepare requires --repo")
        prepare(args.requirements, args.repo, args.vendor)
    else:
        use_wheel(args.requirements, args.vendor)


if __name__ == "__main__":
    main()
