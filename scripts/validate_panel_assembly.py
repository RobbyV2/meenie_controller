#!/usr/bin/env python3
"""Fail closed on customer-panel handling and edge-PTH geometry."""

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys

import pcbnew

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "panel"))
import panel


EDGE_PTH_REFERENCES = {
    "E2", "E3", "E4", "E5", "E6", "E8", "E9",
    "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W9",
}
EDGE_PTH_PAD_MM = 0.600
EDGE_PTH_DRILL_MM = 0.300
EDGE_PTH_Y_MM = 91.114999
EDGE_PTH_EXACT_CLEARANCE_MM = 0.249999
EDGE_PTH_VENDOR_MINIMUM_MM = 0.200
FIDUCIAL_COPPER_MM = 1.000
FIDUCIAL_MASK_OPENING_MM = 2.000
TOOLING_HOLE_MM = 3.050


def close(a, b, tolerance=1e-6):
    return math.isclose(float(a), float(b), abs_tol=tolerance)


def mm_pair(value):
    return tuple(round(item, 6) for item in pcbnew.ToMM(value))


def footprint_map(board):
    result = {}
    for item in board.GetFootprints():
        result.setdefault(item.GetReference(), []).append(item)
    return result


def single_pad(footprint):
    pads = list(footprint.Pads())
    if len(pads) != 1:
        raise ValueError(f"{footprint.GetReference()} has {len(pads)} pads, not 1")
    return pads[0]


def expected_bites():
    result = []
    for placement in panel.placements():
        for start, _end in panel.TABS:
            bite0 = (panel.TAB_W - (panel.BITE_N - 1) * panel.BITE_P) / 2.0
            for index in range(panel.BITE_N):
                result.append(panel.K(placement["x0"] + start + bite0 + index * panel.BITE_P,
                                      placement["north"] + panel.BITE_OFFSET))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--panel", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    unit = pcbnew.LoadBoard(str(args.board))
    customer_panel = pcbnew.LoadBoard(str(args.panel))
    issues = []

    x0, y0, width, height = panel.outline(str(args.board))
    if (round(width, 6), round(height, 6)) != (round(panel.BW, 6), round(panel.BH, 6)):
        issues.append("unit-board outline differs from panel-generator source geometry")
    unit_map = footprint_map(unit)
    observed_edge_refs = {ref for ref in unit_map if re.fullmatch(r"[EW]\d+", ref) and ref != "W8"}
    if observed_edge_refs != EDGE_PTH_REFERENCES:
        issues.append(f"edge PTH references {sorted(observed_edge_refs)} differ from exact released "
                      f"{sorted(EDGE_PTH_REFERENCES)}")
    edge_inventory = []
    for ref in sorted(EDGE_PTH_REFERENCES):
        matches = unit_map.get(ref, [])
        if len(matches) != 1:
            issues.append(f"{ref} footprint count is {len(matches)}, not 1")
            continue
        try:
            pad = single_pad(matches[0])
            position, size, drill = mm_pair(pad.GetPosition()), mm_pair(pad.GetSize()), mm_pair(pad.GetDrillSize())
            clearance = position[1] - y0 - size[1] / 2.0
            edge_inventory.append({"reference": ref, "position_mm": list(position),
                                   "pad_mm": list(size), "drill_mm": list(drill),
                                   "copper_to_edge_mm": round(clearance, 6)})
            if not close(position[1], EDGE_PTH_Y_MM):
                issues.append(f"{ref} Y {position[1]:.6f} mm differs from exact released {EDGE_PTH_Y_MM:.6f} mm")
            if size != (EDGE_PTH_PAD_MM, EDGE_PTH_PAD_MM) or drill != (EDGE_PTH_DRILL_MM, EDGE_PTH_DRILL_MM):
                issues.append(f"{ref} pad/drill {size}/{drill} differs from exact released 0.600/0.300 mm")
            if not close(clearance, EDGE_PTH_EXACT_CLEARANCE_MM):
                issues.append(f"{ref} copper-to-edge clearance {clearance:.6f} mm differs from exact released "
                              f"{EDGE_PTH_EXACT_CLEARANCE_MM:.6f} mm")
            if clearance < EDGE_PTH_VENDOR_MINIMUM_MM - 1e-6:
                issues.append(f"{ref} copper-to-edge clearance {clearance:.6f} mm is below 0.200 mm")
        except (TypeError, ValueError) as exc:
            issues.append(str(exc))

    pmap = footprint_map(customer_panel)
    expected_mb_refs = {f"MB{index}" for index in range(1, panel.BOARDS * len(panel.TABS) * panel.BITE_N + 1)}
    expected_th_refs = {f"TH{index}" for index in range(1, panel.TOOLING_NPTH + 1)}
    expected_fid_refs = {f"FID{index}" for index in range(1, 4)}
    for label, expected, pattern in (
        ("mouse-bite", expected_mb_refs, r"MB\d+"),
        ("tooling-hole", expected_th_refs, r"TH\d+"),
        ("global-fiducial", expected_fid_refs, r"FID\d+"),
    ):
        observed = {ref for ref in pmap if re.fullmatch(pattern, ref)}
        if observed != expected:
            issues.append(f"{label} references differ: observed {len(observed)}, expected {len(expected)}")

    bite_positions = []
    for index, expected_position in enumerate(expected_bites(), 1):
        ref, matches = f"MB{index}", pmap.get(f"MB{index}", [])
        if len(matches) != 1:
            issues.append(f"{ref} footprint count is {len(matches)}, not 1")
            continue
        try:
            pad = single_pad(matches[0])
            position, size, drill = mm_pair(pad.GetPosition()), mm_pair(pad.GetSize()), mm_pair(pad.GetDrillSize())
            bite_positions.append(list(position))
            if position != tuple(round(value, 6) for value in expected_position):
                issues.append(f"{ref} position {position} differs from exact released {expected_position}")
            if size != (panel.BITE_D, panel.BITE_D) or drill != (panel.BITE_D, panel.BITE_D):
                issues.append(f"{ref} size/drill {size}/{drill} differs from exact released {panel.BITE_D:.3f} mm")
        except (TypeError, ValueError) as exc:
            issues.append(str(exc))

    tooling_positions = [panel.K(x, y) for x, y in ((2.5, 2.5), (panel.W - 2.5, 2.5),
                                                    (2.5, panel.H - 2.5),
                                                    (panel.W - 2.5, panel.H - 2.5))]
    for index, expected_position in enumerate(tooling_positions, 1):
        ref, matches = f"TH{index}", pmap.get(f"TH{index}", [])
        if len(matches) != 1:
            issues.append(f"{ref} footprint count is {len(matches)}, not 1")
            continue
        try:
            pad = single_pad(matches[0])
            if mm_pair(pad.GetPosition()) != tuple(round(v, 6) for v in expected_position):
                issues.append(f"{ref} position differs from exact released panel tooling pattern")
            if mm_pair(pad.GetSize()) != (TOOLING_HOLE_MM, TOOLING_HOLE_MM) or \
                    mm_pair(pad.GetDrillSize()) != (TOOLING_HOLE_MM, TOOLING_HOLE_MM):
                issues.append(f"{ref} differs from exact released 3.050 mm NPTH")
        except (TypeError, ValueError) as exc:
            issues.append(str(exc))

    fid_positions = [panel.K(x, y) for x, y in ((10.0, panel.FID_EDGE),
                                                (panel.W - 10.0, panel.FID_EDGE),
                                                (10.0, panel.H - panel.FID_EDGE))]
    for index, expected_position in enumerate(fid_positions, 1):
        ref, matches = f"FID{index}", pmap.get(f"FID{index}", [])
        if len(matches) != 1:
            issues.append(f"{ref} footprint count is {len(matches)}, not 1")
            continue
        pads = list(matches[0].Pads())
        if mm_pair(matches[0].GetPosition()) != tuple(round(v, 6) for v in expected_position):
            issues.append(f"{ref} position differs from exact released asymmetric pattern")
        if len(pads) != 2:
            issues.append(f"{ref} has {len(pads)} face pads, not 2")
            continue
        faces = sorted("F" if pad.IsOnLayer(pcbnew.F_Cu) and not pad.IsOnLayer(pcbnew.B_Cu)
                       else "B" if pad.IsOnLayer(pcbnew.B_Cu) and not pad.IsOnLayer(pcbnew.F_Cu)
                       else "invalid" for pad in pads)
        if faces != ["B", "F"]:
            issues.append(f"{ref} face inventory {faces} differs from exact F/B pair")
        for pad in pads:
            size = mm_pair(pad.GetSize())
            mask_opening = size[0] + 2.0 * pcbnew.ToMM(pad.GetLocalSolderMaskMargin())
            if size != (FIDUCIAL_COPPER_MM, FIDUCIAL_COPPER_MM) or not close(mask_opening, FIDUCIAL_MASK_OPENING_MM):
                issues.append(f"{ref} copper/mask geometry differs from exact 1.000/2.000 mm")

    panel_outline = panel.outline(str(args.panel))[2:]
    if tuple(round(value, 6) for value in panel_outline) != (round(panel.W, 6), round(panel.H, 6)):
        issues.append(f"panel outline {panel_outline} differs from exact released {(panel.W, panel.H)}")

    minimum_edge_clearance = min((item["copper_to_edge_mm"] for item in edge_inventory), default=None)
    result = {
        "unit_board_sha256": hashlib.sha256(args.board.read_bytes()).hexdigest(),
        "panel_board_sha256": hashlib.sha256(args.panel.read_bytes()).hexdigest(),
        "unit_board_mm": [panel.BW, panel.BH],
        "panel_mm": [panel.W, panel.H],
        "grid": [panel.COLS, panel.ROWS],
        "board_count": panel.BOARDS,
        "edge_pth": {"count": len(edge_inventory), "references": sorted(EDGE_PTH_REFERENCES),
                     "pad_mm": EDGE_PTH_PAD_MM, "drill_mm": EDGE_PTH_DRILL_MM,
                     "minimum_copper_to_edge_mm": minimum_edge_clearance,
                     "vendor_minimum_copper_to_edge_mm": EDGE_PTH_VENDOR_MINIMUM_MM,
                     "inventory": edge_inventory},
        "tabs": {"per_board": len(panel.TABS), "width_mm": panel.TAB_W,
                 "edge": "breakout_pth"},
        "mouse_bites": {"count": len(bite_positions), "hole_mm": panel.BITE_D,
                        "pitch_mm": panel.BITE_P, "per_tab": panel.BITE_N,
                        "offset_into_routed_gap_mm": panel.BITE_OFFSET},
        "tooling_holes": {"count": len(expected_th_refs), "diameter_mm": TOOLING_HOLE_MM},
        "global_fiducials": {"count": len(expected_fid_refs), "faces": ["F", "B"],
                             "copper_mm": FIDUCIAL_COPPER_MM,
                             "mask_opening_mm": FIDUCIAL_MASK_OPENING_MM,
                             "asymmetric": True},
        "required_handling": {
            "supported_low_stress_cut_or_nibble_depaneling": True,
            "hand_snapping_prohibited": True,
            "edge_pth_barrel_and_solder_joint_post_depanel_inspection": True,
            "discard_units_with_cracked_laminate_or_damaged_edge_pth": True,
        },
        "issues": issues,
        "passed": not issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
