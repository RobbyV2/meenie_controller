#!/usr/bin/env python3
"""Fail closed on the J1 locating-post containment and U1-side overlap disposition."""

import argparse
import hashlib
import json
import math
import pathlib

import pcbnew


BOARD_THICKNESS_MM = 0.991
POST_NOMINAL_MM = 0.60
POST_TOLERANCE_MM = 0.15
POST_MAXIMUM_MM = POST_NOMINAL_MM + POST_TOLERANCE_MM
MINIMUM_CONTAINMENT_MM = BOARD_THICKNESS_MM - POST_MAXIMUM_MM
J1_POSITION_MM = (107.32, 95.94)
J1_NPTH_MM = ((104.43, 94.72), (110.21, 94.72))
J1_NPTH_DIAMETER_MM = 0.75
U1_POSITION_MM = (105.77, 95.9)


def mm(value):
    return tuple(round(item, 6) for item in pcbnew.ToMM(value))


def footprint(board, reference):
    matches = [item for item in board.GetFootprints() if item.GetReference() == reference]
    if len(matches) != 1:
        raise ValueError(f"{reference} footprint count is {len(matches)}, not 1")
    return matches[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--post-maximum-mm", type=float, default=POST_MAXIMUM_MM,
                        help="fault-injection/alternate exact connector limit")
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))
    issues = []

    thickness = pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness())
    if not math.isclose(thickness, BOARD_THICKNESS_MM, abs_tol=1e-6):
        issues.append(f"board thickness {thickness:.6f} mm differs from exact released "
                      f"{BOARD_THICKNESS_MM:.3f} mm")
    try:
        j1, u1 = footprint(board, "J1"), footprint(board, "U1")
        if mm(j1.GetPosition()) != J1_POSITION_MM:
            issues.append(f"J1 position {mm(j1.GetPosition())} differs from exact released {J1_POSITION_MM}")
        if mm(u1.GetPosition()) != U1_POSITION_MM:
            issues.append(f"U1 position {mm(u1.GetPosition())} differs from exact released {U1_POSITION_MM}")
        holes = sorted((mm(pad.GetPosition()), round(pcbnew.ToMM(pad.GetDrillSize())[0], 6))
                       for pad in j1.Pads() if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH)
        expected_holes = sorted((position, J1_NPTH_DIAMETER_MM) for position in J1_NPTH_MM)
        if holes != expected_holes:
            issues.append(f"J1 locating holes {holes!r} differ from exact released {expected_holes!r}")
    except ValueError as exc:
        issues.append(str(exc))

    containment = thickness - args.post_maximum_mm
    if containment < MINIMUM_CONTAINMENT_MM - 1e-6:
        issues.append(f"J1 locating-post containment {containment:.6f} mm is below released minimum "
                      f"{MINIMUM_CONTAINMENT_MM:.3f} mm")
    if containment <= 0:
        issues.append("J1 locating post can protrude through the board into the bottom-side U1 region")

    result = {
        "board_sha256": hashlib.sha256(args.board.read_bytes()).hexdigest(),
        "board_thickness_mm": round(thickness, 6),
        "connector": {
            "reference": "J1", "manufacturer": "SHOU HAN", "mpn": "TYPE-C 16P QTWT",
            "lcsc_part": "C5187472", "drawing_post_nominal_mm": POST_NOMINAL_MM,
            "drawing_tolerance_mm": POST_TOLERANCE_MM,
            "drawing_post_maximum_mm": args.post_maximum_mm,
            "locating_holes_mm": [list(value) for value in J1_NPTH_MM],
            "locating_hole_diameter_mm": J1_NPTH_DIAMETER_MM,
        },
        "post_containment_minimum_mm": round(containment, 6),
        "issues": issues,
        "passed": not issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
