#!/usr/bin/env python3
"""Fail closed on release-critical source identity and the exact Y1 model."""

import argparse
import csv
import hashlib
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "panel"))
import sexp

PARTS = {
    "C1": {"LCSC Part": "C161404", "Manufacturer": "Murata", "MPN": "GRM0335C1H120GA01D"},
    "C2": {"LCSC Part": "C161404", "Manufacturer": "Murata", "MPN": "GRM0335C1H120GA01D"},
    "Y1": {"LCSC Part": "C255986", "Manufacturer": "Seiko Epson", "MPN": "X1E0002510059"},
}
Y1_FOOTPRINT = "meenie:Crystal_SMD_1612-4Pin_FA-118T"
Y1_MODEL = "${KIPRJMOD}/../lib/3dmodels/meenie.3dshapes/Crystal_SMD_1612-4Pin_1.6x1.2mm.step"
Y1_MODEL_SHA256 = "5dc1840726dd4c28d037b0904ccc2ff9fbfd4a66fc57bfb97c67d25f630c3d97"


def unq(value):
    return sexp.unq(value) if isinstance(value, str) else value


def properties(node):
    return {
        unq(item[1]): unq(item[2])
        for item in sexp.kids(node, "property")
        if len(item) >= 3
    }


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--bom", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    issues = []

    rows = {row["Designator"]: row for row in csv.DictReader(args.bom.open(newline=""))}
    for ref, expected in PARTS.items():
        row = rows.get(ref)
        if row is None:
            issues.append(f"schematic BOM lacks {ref}")
            continue
        for field, value in expected.items():
            if row.get(field, "").strip() != value:
                issues.append(f"schematic {ref} {field} {row.get(field, '')!r} != {value!r}")

    tree = sexp.parse(args.board.read_text())
    footprints = []
    for node in tree:
        if sexp.tag(node) == "footprint":
            props = properties(node)
            if props.get("Reference") in PARTS:
                footprints.append((node, props))
    byref = {props["Reference"]: (node, props) for node, props in footprints}
    for ref, expected in PARTS.items():
        item = byref.get(ref)
        if item is None:
            issues.append(f"PCB lacks footprint {ref}")
            continue
        node, props = item
        for field, value in expected.items():
            if props.get(field, "") != value:
                issues.append(f"PCB {ref} {field} {props.get(field, '')!r} != {value!r}")

    y1 = byref.get("Y1")
    model_path = None
    if y1 is not None:
        node, _ = y1
        if unq(node[1]) != Y1_FOOTPRINT:
            issues.append(f"Y1 footprint {unq(node[1])!r} != {Y1_FOOTPRINT!r}")
        models = sexp.kids(node, "model")
        paths = [unq(model[1]) for model in models if len(model) >= 2]
        if paths != [Y1_MODEL]:
            issues.append(f"Y1 model paths {paths!r} != {[Y1_MODEL]!r}")
        else:
            model_path = pathlib.Path(Y1_MODEL.replace("${KIPRJMOD}", str(args.board.parent))).resolve()
            if not model_path.is_file():
                issues.append(f"Y1 exact model missing: {model_path}")
            elif sha256(model_path) != Y1_MODEL_SHA256:
                issues.append(f"Y1 exact model SHA-256 {sha256(model_path)} != {Y1_MODEL_SHA256}")

    result = {
        "passed": not issues,
        "issues": issues,
        "critical_parts": PARTS,
        "y1_footprint": Y1_FOOTPRINT,
        "y1_model": Y1_MODEL,
        "y1_model_resolved": os.path.relpath(model_path, args.board.parent.parent) if model_path else None,
        "y1_model_sha256": sha256(model_path) if model_path and model_path.is_file() else None,
    }
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
