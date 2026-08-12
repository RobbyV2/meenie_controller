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
    "C1": ("C161404", "Murata", "GRM0335C1H120GA01D"),
    "C2": ("C161404", "Murata", "GRM0335C1H120GA01D"),
    "C3": ("C85923", "Murata Electronics", "GRM033C71C104KE14D"),
    "C4": ("C85923", "Murata Electronics", "GRM033C71C104KE14D"),
    "C5": ("C272870", "Yageo", "CC0201JRNPO9BN101"),
    "C6": ("C90057", "Murata Electronics", "GRM188R61E475KE11D"),
    "C7": ("C52923", "Samsung Electro-Mechanics", "CL05A105KA5NQNC"),
    "C8": ("C1525", "Samsung Electro-Mechanics", "CL05B104KO5NNNC"),
    "C9": ("C90057", "Murata Electronics", "GRM188R61E475KE11D"),
    "C11": ("C90057", "Murata Electronics", "GRM188R61E475KE11D"),
    "C12": ("C85923", "Murata Electronics", "GRM033C71C104KE14D"),
    "C14": ("C408126", "Murata Electronics", "GJM0335C1ER80WB01D"),
    "C15": ("C237424", "Murata Electronics", "GJM0335C1HR50WB01D"),
    "C16": ("C408126", "Murata Electronics", "GJM0335C1ER80WB01D"),
    "C18": ("C85923", "Murata Electronics", "GRM033C71C104KE14D"),
    "C19": ("C318563", "Samsung Electro-Mechanics", "CL05A475MO5NUNC"),
    "C21": ("C384953", "Murata Electronics", "GRM0335C1E821JA01D"),
    "D1": ("C1972949", "Texas Instruments", "TPD1E01B04DPLR"),
    "D2": ("C22427765", "Texas Instruments", "ESD441DPLR"),
    "D3": ("C364556", "Lite-On", "LTST-C281KRKT"),
    "D4": ("C239819", "Will Semiconductor", "ESD5311Z-2/TR"),
    "J1": ("C5187472", "SHOU HAN", "TYPE-C 16P QTWT"),
    "L1": ("C237443", "Murata Electronics", "LQP03HQ4N7H02D"),
    "L2": ("C237443", "Murata Electronics", "LQP03HQ4N7H02D"),
    "R1": ("C327251", "Yageo", "RC0402FR-072R2L"),
    "R2": ("C274341", "Yageo", "RC0201FR-075K1L"),
    "R3": ("C274341", "Yageo", "RC0201FR-075K1L"),
    "R4": ("C270365", "Uniroyal Electronics", "0201WMF1001TEE"),
    "SW1": ("", "C&K/Littelfuse", "GSNANOT000102"),
    "U1": ("C190794", "Nordic Semiconductor", "NRF52840-QIAA-R"),
    "Y1": ("C255986", "Seiko Epson", "X1E0002510059"),
}
PARTS = {ref: dict(zip(("LCSC Part", "Manufacturer", "MPN"), values)) for ref, values in PARTS.items()}
Y1_FOOTPRINT = "meenie:Crystal_SMD_1612-4Pin_FA-118T"
Y1_MODEL = "${KIPRJMOD}/../lib/3dmodels/meenie.3dshapes/Crystal_SMD_1612-4Pin_1.6x1.2mm.step"
Y1_MODEL_SHA256 = "5dc1840726dd4c28d037b0904ccc2ff9fbfd4a66fc57bfb97c67d25f630c3d97"
EXPECTED_FOOTPRINTS = {
    "C19": "meenie:C0402",
    "D1": "meenie:ESD_X2SON_DPL",
    "D2": "meenie:ESD_X2SON_DPL",
    "D3": "meenie:LED0402-R-RD_RED",
    "D4": "meenie:ESD_DFN0603-2",
    "J1": "meenie:USB-TYPE-C-SMD_TYPE-C-16P-QTWT",
    "SW1": "meenie:Switch_NanoT_AS",
    "U1": "meenie:AQFN-73_L7.0-W7.0-P0.50-BL-EP4.8",
    "Y1": Y1_FOOTPRINT,
}
EXPECTED_DATASHEETS = {
    "C6": "https://www.murata.com/en-us/products/productdetail?partno=GRM188R61E475KE11D",
    "C9": "https://www.murata.com/en-us/products/productdetail?partno=GRM188R61E475KE11D",
    "C11": "https://www.murata.com/en-us/products/productdetail?partno=GRM188R61E475KE11D",
    "C19": "https://product.samsungsem.com/mlcc/CL05A475MO5NUN.do",
    "D1": "https://www.ti.com/lit/ds/symlink/tpd1e01b04.pdf",
    "D2": "https://www.ti.com/lit/ds/symlink/esd441.pdf",
    "D3": "https://optoelectronics.liteon.com/upload/download/DS22-2008-0117/LTST-C281KRKT.PDF",
    "D4": "https://www.lcsc.com/datasheet/C239819.pdf",
    "J1": "https://atta.szlcsc.com/upload/public/pdf/source/20250724/0D22D8172E0B6194F7E17C81DE47450F.pdf",
    "SW1": "https://www.ckswitches.com/media/2866/nanot.pdf",
    "Y1": "https://download.epsondevice.com/td/pdf/brief/FA-118T_en.pdf",
}
U1_LAND_PATTERN_REFERENCE = "https://docs.nordicsemi.com/r/bundle/nan_040/page/app/nan_040/nan40_aqfn73_footprint.html"
U1_SIGNAL_PAD_COUNT = 73
U1_SIGNAL_PAD_SIZE_MM = 0.25
U1_SIGNAL_MASK_MARGIN_MM = 0.0625
U1_SIGNAL_PASTE_MARGIN_MM = 0.025
EXPECTED_PAD_GEOMETRY = {
    "C19": {
        ("1", "smd", -0.42, 0.0, 90.0, 0.50, 0.54, None),
        ("2", "smd", 0.42, 0.0, 90.0, 0.50, 0.54, None),
    },
    "D1": {
        ("1", "smd", -0.22, 0.0, 90.0, 0.28, 0.25, None),
        ("2", "smd", 0.22, 0.0, 90.0, 0.28, 0.25, None),
    },
    "D2": {
        ("1", "smd", -0.22, 0.0, 90.0, 0.28, 0.25, None),
        ("2", "smd", 0.22, 0.0, 90.0, 0.28, 0.25, None),
    },
    "D3": {
        ("1", "smd", 0.50, 0.04, 90.0, 0.65, 0.65, None),
        ("2", "smd", -0.50, 0.04, 90.0, 0.65, 0.65, None),
    },
    "D4": {
        ("1", "smd", -0.21, 0.0, 270.0, 0.20, 0.32, None),
        ("2", "smd", 0.21, 0.0, 270.0, 0.20, 0.32, None),
    },
    "J1": {
        ("", "np_thru_hole", -2.89, -1.22, 0.0, 0.75, 0.75, 0.75),
        ("", "np_thru_hole", 2.89, -1.22, 0.0, 0.75, 0.75, 0.75),
        ("0", "smd", -5.4, 1.65, 0.0, 1.3, 0.18, None),
        ("0", "smd", -5.2, -1.89, 0.0, 1.7, 1.2, None),
        ("0", "smd", -5.2, 2.4, 0.0, 1.7, 1.32, None),
        ("0", "smd", 5.2, -1.89, 0.0, 1.7, 1.2, None),
        ("0", "smd", 5.2, 2.31, 0.0, 1.7, 1.5, None),
        ("A1B12", "smd", -3.2, -2.34, 0.0, 0.6, 1.08, None),
        ("A4B9", "smd", -2.4, -2.34, 0.0, 0.6, 1.08, None),
        ("A5", "smd", -1.25, -2.31, 0.0, 0.3, 1.14, None),
        ("A6", "smd", -0.25, -2.31, 0.0, 0.3, 1.14, None),
        ("A7", "smd", 0.25, -2.31, 0.0, 0.3, 1.14, None),
        ("A8", "smd", 1.25, -2.31, 0.0, 0.3, 1.14, None),
        ("B1A12", "smd", 3.2, -2.34, 0.0, 0.6, 1.08, None),
        ("B4A9", "smd", 2.4, -2.34, 0.0, 0.6, 1.08, None),
        ("B5", "smd", 1.75, -2.31, 0.0, 0.3, 1.14, None),
        ("B6", "smd", 0.75, -2.31, 0.0, 0.3, 1.14, None),
        ("B7", "smd", -0.75, -2.31, 0.0, 0.3, 1.14, None),
        ("B8", "smd", -1.75, -2.31, 0.0, 0.3, 1.14, None),
    },
    "SW1": {
        ("1", "smd", -1.15, 0.0, 90.0, 0.5, 1.4, None),
        ("2", "smd", 1.15, 0.0, 90.0, 0.5, 1.4, None),
    },
    "Y1": {
        ("1", "smd", -0.55, -0.40, 90.0, 0.65, 0.55, None),
        ("2", "smd", 0.55, -0.40, 90.0, 0.65, 0.55, None),
        ("3", "smd", 0.55, 0.40, 90.0, 0.65, 0.55, None),
        ("4", "smd", -0.55, 0.40, 90.0, 0.65, 0.55, None),
    },
}


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


def pad_geometry(node):
    result = set()
    for pad in sexp.kids(node, "pad"):
        at, size, drill = sexp.kid(pad, "at"), sexp.kid(pad, "size"), sexp.kid(pad, "drill")
        angle = float(at[3]) if at is not None and len(at) >= 4 else 0.0
        hole = float(drill[1]) if drill is not None and len(drill) >= 2 else None
        result.add((
            unq(pad[1]), unq(pad[2]), float(at[1]), float(at[2]), angle,
            float(size[1]), float(size[2]), hole,
        ))
    return result


def u1_signal_land_pattern(node):
    rows = []
    for pad in sexp.kids(node, "pad"):
        number = unq(pad[1])
        if not number or number == "0":
            continue
        at, size, layers = sexp.kid(pad, "at"), sexp.kid(pad, "size"), sexp.kid(pad, "layers")
        mask = sexp.kid(pad, "solder_mask_margin")
        paste = sexp.kid(pad, "solder_paste_margin")
        rows.append({
            "number": number,
            "type": unq(pad[2]),
            "shape": unq(pad[3]),
            "size_mm": [float(size[1]), float(size[2])],
            "layers": [unq(value) for value in layers[1:]],
            "mask_margin_mm": float(mask[1]) if mask else None,
            "paste_margin_mm": float(paste[1]) if paste else None,
            "position_mm": [float(at[1]), float(at[2])],
        })
    return rows


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

    for ref, expected in EXPECTED_FOOTPRINTS.items():
        item = byref.get(ref)
        if item is not None and unq(item[0][1]) != expected:
            issues.append(f"{ref} footprint {unq(item[0][1])!r} != {expected!r}")
    for ref, expected in EXPECTED_DATASHEETS.items():
        item = byref.get(ref)
        if item is not None and item[1].get("Datasheet", "") != expected:
            issues.append(f"{ref} datasheet {item[1].get('Datasheet', '')!r} != {expected!r}")
    for ref, expected in EXPECTED_PAD_GEOMETRY.items():
        item = byref.get(ref)
        if item is not None and pad_geometry(item[0]) != expected:
            issues.append(f"{ref} embedded pad geometry differs from the reviewed manufacturer land pattern")

    u1_land = []
    if "U1" in byref:
        u1_land = u1_signal_land_pattern(byref["U1"][0])
        if len(u1_land) != U1_SIGNAL_PAD_COUNT:
            issues.append(f"U1 signal pad count {len(u1_land)} != {U1_SIGNAL_PAD_COUNT}")
        for pad in u1_land:
            if (pad["type"] != "smd" or pad["shape"] != "circle" or
                    pad["size_mm"] != [U1_SIGNAL_PAD_SIZE_MM, U1_SIGNAL_PAD_SIZE_MM] or
                    pad["layers"] != ["B.Cu", "B.Mask", "B.Paste"] or
                    pad["mask_margin_mm"] != U1_SIGNAL_MASK_MARGIN_MM or
                    pad["paste_margin_mm"] != U1_SIGNAL_PASTE_MARGIN_MM):
                issues.append(f"U1 signal pad {pad['number']} differs from the released land geometry")

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
        "exact_footprints": EXPECTED_FOOTPRINTS,
        "exact_datasheets": EXPECTED_DATASHEETS,
        "exact_pad_geometry": {ref: sorted(values, key=str) for ref, values in EXPECTED_PAD_GEOMETRY.items()},
        "u1_signal_land_pattern": {
            "manufacturer_reference": U1_LAND_PATTERN_REFERENCE,
            "pad_count": len(u1_land),
            "copper_pad_diameter_mm": U1_SIGNAL_PAD_SIZE_MM,
            "solder_mask_opening_mm": U1_SIGNAL_PAD_SIZE_MM + 2 * U1_SIGNAL_MASK_MARGIN_MM,
            "paste_mask_opening_mm": U1_SIGNAL_PAD_SIZE_MM + 2 * U1_SIGNAL_PASTE_MARGIN_MM,
            "nordic_recommended_copper_pad_diameter_mm": 0.275,
            "reduced_copper_land": True,
        },
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
