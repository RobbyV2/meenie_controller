#!/usr/bin/env python3
"""Verify that the released RF_50 trunk has continuous In6 GND reference."""

import argparse
import hashlib
import json
import math
import pathlib

import pcbnew


def mm(value):
    return pcbnew.ToMM(value)


def point_mm(point):
    return (round(mm(point.x), 6), round(mm(point.y), 6))


def canonical_track(track, board):
    endpoints = tuple(sorted((point_mm(track.GetStart()), point_mm(track.GetEnd()))))
    return (board.GetLayerName(track.GetLayer()), endpoints, round(mm(track.GetWidth()), 6), track.IsLocked())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True)
    parser.add_argument("--step-mm", type=float, default=0.025)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()
    board = pcbnew.LoadBoard(args.board)
    rf = [track for track in board.GetTracks()
          if isinstance(track, pcbnew.PCB_TRACK) and track.GetNetname() == "RF_50"]
    if len(rf) != 5:
        raise SystemExit(f"FAIL: RF_50 segment count {len(rf)} != 5")
    if any(track.GetLayer() != pcbnew.B_Cu or not math.isclose(mm(track.GetWidth()), 0.17, abs_tol=1e-6)
           for track in rf):
        raise SystemExit("FAIL: RF_50 is not exact 0.170 mm B.Cu artwork")
    references = [zone for zone in board.Zones()
                  if zone.GetNetname() == "GND" and zone.HasFilledPolysForLayer(pcbnew.In6_Cu)]
    if len(references) != 1:
        raise SystemExit(f"FAIL: expected one filled In6 GND reference zone, found {len(references)}")
    reference = references[0]
    samples = 0
    for track in rf:
        start, end = track.GetStart(), track.GetEnd()
        length = mm(track.GetLength())
        count = max(1, math.ceil(length / args.step_mm))
        for index in range(count + 1):
            fraction = index / count
            point = pcbnew.VECTOR2I(round(start.x + (end.x - start.x) * fraction),
                                    round(start.y + (end.y - start.y) * fraction))
            samples += 1
            if not reference.HitTestFilledArea(pcbnew.In6_Cu, point):
                raise SystemExit(f"FAIL: RF_50 lacks filled In6 GND under {mm(point.x):.6f}, {mm(point.y):.6f} mm")
    match_refs = ("C14", "L1", "C15", "C16", "L2")
    match = {}
    for ref in match_refs:
        footprint = board.FindFootprintByReference(ref)
        if footprint is None:
            raise SystemExit(f"FAIL: missing RF match footprint {ref}")
        center = footprint.GetPosition()
        pads = list(footprint.Pads())
        points = [center] + [pad.GetPosition() for pad in pads]
        if not all(reference.HitTestFilledArea(pcbnew.In6_Cu, point) for point in points):
            raise SystemExit(f"FAIL: retained In6 reference is not continuous beneath {ref}")
        match[ref] = {
            "center_mm": [mm(center.x), mm(center.y)],
            "pad_count": len(pads),
            "in6_ground_below_center_and_pads": True,
        }
    ant_items = [item for item in board.GetTracks() if item.GetNetname() == "ANT_FEED"]
    ant_vias = [item for item in ant_items if isinstance(item, pcbnew.PCB_VIA)]
    ant_tracks = [item for item in ant_items if isinstance(item, pcbnew.PCB_TRACK)
                  and not isinstance(item, pcbnew.PCB_VIA)]
    expected_ant_tracks = {
        ("F.Cu", ((100.625, 92.79), (100.625, 99.375)), 0.85, True),
        ("F.Cu", ((100.625, 92.79), (101.65, 92.043)), 0.115, False),
        ("B.Cu", ((101.52, 92.043), (101.65, 92.043)), 0.115, False),
        ("B.Cu", ((101.52, 92.043), (102.138, 92.043)), 0.115, False),
    }
    actual_ant_tracks = {canonical_track(track, board) for track in ant_tracks}
    if actual_ant_tracks != expected_ant_tracks:
        raise SystemExit(f"FAIL: ANT_FEED track topology {sorted(actual_ant_tracks)!r} differs from released geometry")
    if len(ant_vias) != 1:
        raise SystemExit(f"FAIL: ANT_FEED via count {len(ant_vias)} != 1")
    ant_via = ant_vias[0]
    ant_via_geometry = {
        "center_mm": list(point_mm(ant_via.GetPosition())),
        "diameter_mm": round(mm(ant_via.GetWidth(ant_via.TopLayer())), 6),
        "drill_mm": round(mm(ant_via.GetDrillValue()), 6),
        "layers": [board.GetLayerName(ant_via.TopLayer()), board.GetLayerName(ant_via.BottomLayer())],
    }
    expected_ant_via = {
        "center_mm": [101.65, 92.043], "diameter_mm": 0.33,
        "drill_mm": 0.15, "layers": ["F.Cu", "B.Cu"],
    }
    if ant_via_geometry != expected_ant_via:
        raise SystemExit(f"FAIL: ANT_FEED via {ant_via_geometry!r} differs from {expected_ant_via!r}")

    copper_layers = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
                     pcbnew.In4_Cu, pcbnew.In5_Cu, pcbnew.In6_Cu, pcbnew.B_Cu)
    corridor = (100.0, 92.215, 1.27, 7.785)
    antenna_keepouts = []
    for layer in copper_layers:
        matches = []
        for zone in board.Zones():
            if not zone.GetIsRuleArea() or not zone.GetLayerSet().Contains(layer):
                continue
            box = zone.GetBoundingBox()
            geometry = tuple(round(value, 6) for value in
                             (mm(box.GetX()), mm(box.GetY()), mm(box.GetWidth()), mm(box.GetHeight())))
            if geometry == corridor:
                matches.append(zone)
        if len(matches) != 1:
            raise SystemExit(f"FAIL: {board.GetLayerName(layer)} antenna keepout count {len(matches)} != 1")
        zone = matches[0]
        expected_track_via_block = layer != pcbnew.F_Cu
        policy = {
            "block_zone_fills": zone.GetDoNotAllowZoneFills(),
            "block_tracks": zone.GetDoNotAllowTracks(),
            "block_vias": zone.GetDoNotAllowVias(),
            "block_pads": zone.GetDoNotAllowPads(),
            "block_footprints": zone.GetDoNotAllowFootprints(),
        }
        expected_policy = {
            "block_zone_fills": True,
            "block_tracks": expected_track_via_block,
            "block_vias": expected_track_via_block,
            "block_pads": False,
            "block_footprints": False,
        }
        if policy != expected_policy:
            raise SystemExit(f"FAIL: {board.GetLayerName(layer)} antenna keepout policy {policy!r} != {expected_policy!r}")
        antenna_keepouts.append({"layer": board.GetLayerName(layer), **policy})

    radiator_samples = 0
    for x_index in range(13):
        x = 100.025 + x_index * 0.1
        for y_index in range(78):
            y = 92.24 + y_index * 0.1
            point = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
            radiator_samples += 1
            for layer in copper_layers:
                for zone in board.Zones():
                    if zone.GetIsRuleArea() or not zone.HasFilledPolysForLayer(layer):
                        continue
                    if zone.HitTestFilledArea(layer, point):
                        raise SystemExit(
                            f"FAIL: filled {board.GetLayerName(layer)} {zone.GetNetname()} copper "
                            f"inside antenna keepout at {x:.3f}, {y:.3f} mm")
    result = {
        "passed": True,
        "board_sha256": hashlib.sha256(pathlib.Path(args.board).read_bytes()).hexdigest(),
        "rf_50_segment_count": len(rf),
        "rf_50_width_mm": 0.17,
        "rf_50_in6_sample_count": samples,
        "rf_50_maximum_sample_step_mm": args.step_mm,
        "matching_components": match,
        "etched_antenna": {
            "radiator_layer": "F.Cu",
            "radiator_width_mm": 0.85,
            "radiator_length_mm": 6.585,
            "radiator_locked": True,
            "ant_feed_tracks": [
                {"layer": layer, "endpoints_mm": [list(point) for point in endpoints],
                 "width_mm": width, "locked": locked}
                for layer, endpoints, width, locked in sorted(actual_ant_tracks)
            ],
            "feed_via": ant_via_geometry,
            "keepout_bbox_mm": list(corridor),
            "keepouts": antenna_keepouts,
            "no_filled_copper_sample_points_per_layer": radiator_samples,
        },
        "nordic_inner_keepout_deviation": True,
    }
    if args.out:
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"PASS: continuous filled In6 GND under {samples} RF_50 samples and all "
          f"{len(match_refs)} match components; exact etched antenna plus {radiator_samples} "
          "keepout samples per layer; explicit Nordic keepout deviation")


if __name__ == "__main__":
    main()
