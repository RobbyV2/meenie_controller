#!/usr/bin/env python3
"""Fail closed on the released USB-C data and ESD geometry."""

import argparse
import hashlib
import heapq
import json
import math
import pathlib

import pcbnew


USB = {
    "USB_D+": {
        "segment_count": 6,
        "connector_pads": (("A6", (107.07, 93.63)), ("B6", (108.07, 93.63))),
        "connector_via": (109.4477, 94.8837),
        "mcu_landing": (102.504, 97.7),
        "mcu_pad": ("AD6", (102.52, 97.65)),
        "protector": ("D1", "1", (108.10, 92.70)),
        "protector_branch_start": (108.07, 93.63),
        "protector_branch_max_mm": 0.931,
        "connector_to_mcu_max_mm": 12.237,
    },
    "USB_D-": {
        "segment_count": 10,
        "connector_pads": (("A7", (107.57, 93.63)), ("B7", (106.57, 93.63))),
        "connector_via": (109.6252, 95.309),
        "mcu_landing": (102.52, 98.15),
        "mcu_pad": ("AD4", (102.52, 98.15)),
        "protector": ("D4", "2", (107.55, 92.66)),
        "protector_branch_start": (107.57, 93.63),
        "protector_branch_max_mm": 0.971,
        "connector_to_mcu_max_mm": 20.694,
    },
}

GROUND_RETURNS = {
    "D1": {"pad": "2", "via": (107.83, 92.24), "max_track_mm": 0.271},
    "D4": {"pad": "1", "via": (107.83, 92.24), "max_track_mm": 0.281},
    "D2": {"pad": "2", "via": (109.72, 92.26), "max_track_mm": 0.001},
}

VBUS_PROTECTION = {
    "connector_pad": ("B4A9", (109.72, 93.60)),
    "protector": ("D2", "1", (109.72, 92.70)),
    "branch_max_mm": 0.901,
}


def mm(value):
    return pcbnew.ToMM(value)


def xy(value):
    point = mm(value)
    return round(point[0], 6), round(point[1], 6)


def close(a, b, tolerance=1e-6):
    return math.dist(a, b) <= tolerance


def pad(board, reference, number):
    footprint = board.FindFootprintByReference(reference)
    if footprint is None:
        raise ValueError(f"missing footprint {reference}")
    matches = [item for item in footprint.Pads() if item.GetNumber() == number]
    if len(matches) != 1:
        raise ValueError(f"{reference} pad {number} count is {len(matches)}, not 1")
    return matches[0]


def graph(segments):
    result = {}
    for item in segments:
        start, end, length = item["start"], item["end"], item["length_mm"]
        result.setdefault(start, []).append((end, length))
        result.setdefault(end, []).append((start, length))
    return result


def shortest(routes, start, end):
    queue = [(0.0, start)]
    seen = {}
    while queue:
        distance, node = heapq.heappop(queue)
        if node in seen:
            continue
        seen[node] = distance
        if node == end:
            return distance
        for neighbor, weight in routes.get(node, []):
            if neighbor not in seen:
                heapq.heappush(queue, (distance + weight, neighbor))
    raise ValueError(f"no copper path from {start} to {end}")


def vias_at(board, net, position):
    return [item for item in board.GetTracks()
            if isinstance(item, pcbnew.PCB_VIA)
            and item.GetNetname() == net and close(xy(item.GetPosition()), position)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))
    issues, result = [], {
        "board_sha256": hashlib.sha256(args.board.read_bytes()).hexdigest(),
        "interface": "USB_2_0_FS",
        "bit_rate_mbit_s": 12,
        "nets": {},
        "ground_returns": {},
    }

    for net, expected in USB.items():
        segments = []
        for item in board.GetTracks():
            if isinstance(item, pcbnew.PCB_VIA) or not isinstance(item, pcbnew.PCB_TRACK) \
                    or item.GetNetname() != net:
                continue
            segments.append({
                "start": xy(item.GetStart()),
                "end": xy(item.GetEnd()),
                "length_mm": mm(item.GetLength()),
                "width_mm": mm(item.GetWidth()),
                "layer": board.GetLayerName(item.GetLayer()),
            })
        if len(segments) != expected["segment_count"]:
            issues.append(f"{net} has {len(segments)} segments, not {expected['segment_count']}")
        widths = sorted({round(item["width_mm"], 6) for item in segments})
        if widths != [0.15]:
            issues.append(f"{net} track widths {widths!r} differ from [0.15] mm")
        routes = graph(segments)
        try:
            branch = shortest(routes, expected["protector_branch_start"], expected["protector"][2])
            connector_paths = {
                number: shortest(routes, position, expected["mcu_landing"])
                for number, position in expected["connector_pads"]
            }
        except ValueError as exc:
            issues.append(f"{net}: {exc}")
            branch, connector_paths = math.inf, {}
        if branch > expected["protector_branch_max_mm"] + 1e-6:
            issues.append(f"{net} protector branch {branch:.6f} mm exceeds released maximum "
                          f"{expected['protector_branch_max_mm']:.3f} mm")
        if connector_paths and max(connector_paths.values()) > expected["connector_to_mcu_max_mm"] + 1e-6:
            issues.append(f"{net} connector-to-MCU path {max(connector_paths.values()):.6f} mm exceeds "
                          f"released maximum {expected['connector_to_mcu_max_mm']:.3f} mm")

        for number, position in expected["connector_pads"]:
            actual = pad(board, "J1", number)
            if actual.GetNetname() != net or not close(xy(actual.GetPosition()), position):
                issues.append(f"J1 {number} is not exact released {net} pad at {position}")
        protector_ref, protector_pad, protector_position = expected["protector"]
        actual = pad(board, protector_ref, protector_pad)
        if actual.GetNetname() != net or not close(xy(actual.GetPosition()), protector_position):
            issues.append(f"{protector_ref} pad {protector_pad} is not exact released {net} endpoint")
        mcu_number, mcu_position = expected["mcu_pad"]
        actual = pad(board, "U1", mcu_number)
        if actual.GetNetname() != net or not close(xy(actual.GetPosition()), mcu_position):
            issues.append(f"U1 {mcu_number} is not exact released {net} pad at {mcu_position}")

        full = vias_at(board, net, expected["connector_via"])
        if len(full) != 1 or full[0].TopLayer() != pcbnew.F_Cu or full[0].BottomLayer() != pcbnew.B_Cu \
                or not math.isclose(mm(full[0].GetWidth(pcbnew.F_Cu)), 0.33, abs_tol=1e-6) \
                or not math.isclose(mm(full[0].GetDrillValue()), 0.15, abs_tol=1e-6):
            issues.append(f"{net} connector breakout lacks exact 0.330/0.150 mm F.Cu-B.Cu via")

        result["nets"][net] = {
            "segment_count": len(segments),
            "track_widths_mm": widths,
            "total_planar_copper_mm": round(sum(item["length_mm"] for item in segments), 6),
            "connector_to_mcu_paths_mm": {key: round(value, 6) for key, value in connector_paths.items()},
            "protector": protector_ref,
            "protector_branch_mm": round(branch, 6),
            "protector_branch_limit_mm": expected["protector_branch_max_mm"],
        }

    for reference, expected in GROUND_RETURNS.items():
        ground_pad = pad(board, reference, expected["pad"])
        position = xy(ground_pad.GetPosition())
        if ground_pad.GetNetname() != "GND":
            issues.append(f"{reference} pad {expected['pad']} is not GND")
        candidates = vias_at(board, "GND", expected["via"])
        if not candidates:
            issues.append(f"{reference} lacks exact released local GND via at {expected['via']}")
        direct = 0.0 if close(position, expected["via"]) else math.inf
        if not math.isfinite(direct):
            for item in board.GetTracks():
                if isinstance(item, pcbnew.PCB_VIA) or not isinstance(item, pcbnew.PCB_TRACK) \
                        or item.GetNetname() != "GND":
                    continue
                ends = (xy(item.GetStart()), xy(item.GetEnd()))
                if (close(ends[0], position) and close(ends[1], expected["via"])) or \
                        (close(ends[1], position) and close(ends[0], expected["via"])):
                    direct = min(direct, mm(item.GetLength()))
        if direct > expected["max_track_mm"] + 1e-6:
            issues.append(f"{reference} GND return {direct:.6f} mm exceeds released maximum "
                          f"{expected['max_track_mm']:.3f} mm")
        result["ground_returns"][reference] = {
            "via_mm": list(expected["via"]),
            "planar_track_mm": round(direct, 6),
            "limit_mm": expected["max_track_mm"],
        }

    vbus_segments = []
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_TRACK) and not isinstance(item, pcbnew.PCB_VIA) \
                and item.GetNetname() == "VBUS":
            vbus_segments.append({"start": xy(item.GetStart()), "end": xy(item.GetEnd()),
                                  "length_mm": mm(item.GetLength())})
    connector_number, connector_position = VBUS_PROTECTION["connector_pad"]
    protector_reference, protector_number, protector_position = VBUS_PROTECTION["protector"]
    try:
        vbus_branch = shortest(graph(vbus_segments), connector_position, protector_position)
    except ValueError as exc:
        issues.append(f"VBUS: {exc}")
        vbus_branch = math.inf
    if vbus_branch > VBUS_PROTECTION["branch_max_mm"] + 1e-6:
        issues.append(f"VBUS protector branch {vbus_branch:.6f} mm exceeds released maximum "
                      f"{VBUS_PROTECTION['branch_max_mm']:.3f} mm")
    for reference, number, position, net in (
        ("J1", connector_number, connector_position, "VBUS"),
        (protector_reference, protector_number, protector_position, "VBUS"),
    ):
        actual = pad(board, reference, number)
        if actual.GetNetname() != net or not close(xy(actual.GetPosition()), position):
            issues.append(f"{reference} pad {number} is not exact released {net} endpoint")
    result["vbus_protection"] = {
        "protector": protector_reference,
        "protector_branch_mm": round(vbus_branch, 6),
        "protector_branch_limit_mm": VBUS_PROTECTION["branch_max_mm"],
    }

    result["issues"] = issues
    result["passed"] = not issues
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
