#!/usr/bin/env python3
"""Run a fresh KiCad DRC and validate it against an existing approved manifest."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("board_dir", nargs="?", type=Path, default=root / "project")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--drc-output", type=Path, default=root / "validation" / "drc-current.json")
    parser.add_argument(
        "--kicad-cli",
        default="/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    )
    args = parser.parse_args()
    board = args.board_dir / "meenie.kicad_pcb"
    manifest = args.manifest or args.board_dir / "stack-manifest.json"
    args.drc_output.parent.mkdir(parents=True, exist_ok=True)
    before = sha256(board)
    command = [
        args.kicad_cli,
        "pcb",
        "drc",
        "--severity-all",
        "--exit-code-violations",
        "--schematic-parity",
        "--format",
        "json",
        "--output",
        str(args.drc_output),
        str(board),
    ]
    drc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    print(drc.stdout, end="")
    if drc.returncode not in (0, 5):
        print(f"FAIL: kicad-cli DRC exited {drc.returncode}")
        return 1
    after = sha256(board)
    if after != before:
        print(f"FAIL: DRC mutated board: {before} -> {after}")
        return 1
    validator = subprocess.run(
        [
            sys.executable,
            str(root / "validate_stacks.py"),
            str(args.board_dir),
            "--manifest",
            str(manifest),
            "--drc-json",
            str(args.drc_output),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(validator.stdout, end="")
    return validator.returncode


if __name__ == "__main__":
    sys.exit(main())
