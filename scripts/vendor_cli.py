#!/usr/bin/env python3
"""Build a wheel from the latest CLI main and verify it in offline builds."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

CLI_REPO = "https://github.com/lynse-ai/lynse-cli.git"
CLI_BRANCH = "main"
CLI_REQUIREMENT = f"lynse-cli @ git+{CLI_REPO}@{CLI_BRANCH}"


def read_requirements(requirements: Path) -> list[str]:
    lines = requirements.read_text().splitlines(keepends=True)
    if sum(line.strip() == CLI_REQUIREMENT for line in lines) != 1:
        raise ValueError("Expected one lynse-cli dependency tracking upstream main")
    return lines


def _worktree_snapshot(repo: Path, destination: Path, vendor: Path) -> tuple[str, str]:
    """Build a wheel from a CLI checkout including its uncommitted changes."""
    if not (repo / "pyproject.toml").is_file():
        raise ValueError("--worktree must point at a lynse-cli checkout with pyproject.toml")
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    # The wheel contains uncommitted changes too, so record a fingerprint of the
    # delta: a production build can then be traced back to this exact worktree.
    delta = subprocess.check_output(
        ["git", "-C", str(repo), "diff", "HEAD", "--binary"])
    shutil.copytree(repo, destination, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", ".venv", "build", "dist", "*.egg-info"))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--wheel-dir", str(vendor.resolve()), str(destination)], check=True)
    return revision, hashlib.sha256(delta).hexdigest()


def prepare(requirements: Path, repo: str, vendor: Path, *, worktree: bool = False) -> None:
    read_requirements(requirements)
    vendor.mkdir(parents=True, exist_ok=True)
    if list(vendor.iterdir()):
        raise ValueError("Use an empty vendor directory so stale wheels cannot be included")
    source_kind, worktree_sha256 = "upstream-main", None
    with tempfile.TemporaryDirectory(prefix="lynse-cli-wheel-") as temp:
        root = Path(temp)
        source = root / "source"
        if worktree:
            revision, worktree_sha256 = _worktree_snapshot(Path(repo).resolve(), source, vendor)
            source_kind = "local-worktree"
        else:
            subprocess.run(["git", "clone", "--depth=1", "--no-tags", "--branch", CLI_BRANCH,
                            "--", str(repo), str(source)], check=True)
            revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                               text=True).strip()
            subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                            "--wheel-dir", str(vendor.resolve()), str(source)], check=True)
    wheels = list(vendor.glob("lynse_cli-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one built lynse-cli wheel")
    wheel = wheels[0]
    manifest: dict[str, str] = {
        "requirement": CLI_REQUIREMENT, "revision": revision, "source": source_kind,
        "wheel": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    if worktree_sha256 is not None:
        manifest["worktree_sha256"] = worktree_sha256
    (vendor / "lynse-cli-source.json").write_text(json.dumps(manifest, indent=2) + "\n")


def use_wheel(requirements: Path, vendor: Path) -> None:
    lines = read_requirements(requirements)
    manifest = json.loads((vendor / "lynse-cli-source.json").read_text())
    if manifest.get("requirement") != CLI_REQUIREMENT:
        raise ValueError("Vendored CLI source does not match requirements.txt")
    revision = manifest.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid vendored CLI revision")
    if manifest.get("source") == "local-worktree" and not re.fullmatch(
            r"[0-9a-f]{64}", str(manifest.get("worktree_sha256", ""))):
        raise ValueError("Local worktree wheel is missing its delta fingerprint")
    name = manifest.get("wheel", "")
    if not name or Path(name).name != name:
        raise ValueError("Invalid CLI wheel filename")
    wheel = vendor / name
    if list(vendor.glob("lynse_cli-*.whl")) != [wheel]:
        raise ValueError("Expected exactly the CLI wheel recorded in the manifest")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest.get("sha256"):
        raise ValueError("Vendored CLI wheel checksum does not match the manifest")
    location = str(wheel) if wheel.is_absolute() else "./" + str(wheel)
    requirements.write_text("".join(
        f"{location}\n" if line.strip() == CLI_REQUIREMENT else line for line in lines
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "use"))
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    parser.add_argument("--vendor", type=Path, default=Path("vendor"))
    parser.add_argument("--repo", default=CLI_REPO, help="CLI Git source (defaults to GitHub)")
    parser.add_argument("--worktree", action="store_true",
                        help="prepare: build from a local CLI checkout including uncommitted changes")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.requirements, args.repo, args.vendor, worktree=args.worktree)
    else:
        if args.worktree:
            parser.error("--worktree only applies to prepare")
        use_wheel(args.requirements, args.vendor)


if __name__ == "__main__":
    main()
