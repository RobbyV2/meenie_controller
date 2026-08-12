#!/usr/bin/env python3
"""Fail closed on the released nRF52840 regulator-decoupling routes."""

import argparse
import hashlib
import heapq
import json
import math
import pathlib

import pcbnew


POWER_ROUTES = {
    "DECUSB": {
        "capacitor": ("C9", "1"),
        "mcu_pads": (("AC5",),),
        "maximum_mm": 1.827,
        "released_mm": 1.826126,
        "reference_mm": 3.452293,
    },
    "DEC4_6": {
        "capacitor": ("C7", "1"),
        "mcu_pads": (("B5",), ("E24",)),
        "maximum_mm": 2.484,
        "released_mm": 2.482770,
    },
    "DEC1": {
        "capacitor": ("C18", "1"),
        "mcu_pads": (("C1",),),
        "maximum_mm": 1.624,
        "released_mm": 1.623037,
    },
    "DEC3": {
        "capacitor": ("C5", "1"),
        "mcu_pads": (("D23",),),
        "maximum_mm": 1.059,
        "released_mm": 1.058665,
    },
    "DEC5": {
        "capacitor": ("C21", "1"),
        "mcu_pads": (("N24",),),
        "maximum_mm": 0.823,
        "released_mm": 0.822113,
    },
}
COPPER_LAYERS = (pcbnew.F_Cu, 4, 6, 8, 10, 12, 14, pcbnew.B_Cu)


def point(value):
    return tuple(round(item, 6) for item in pcbnew.ToMM(value))


def pad(board, reference, number):
    footprint = next((item for item in board.GetFootprints()
                      if item.GetReference() == reference), None)
    if footprint is None:
        raise ValueError(f"missing footprint {reference}")
    matches = [item for item in footprint.Pads() if item.GetNumber() == number]
    if len(matches) != 1:
        raise ValueError(f"{reference} pad {number} count is {len(matches)}, not 1")
    return matches[0]


def copper_layers(board, item):
    return [layer for layer in COPPER_LAYERS
            if item.IsOnLayer(layer)]


def add_edge(graph, left, right, weight):
    graph.setdefault(left, []).append((right, weight))
    graph.setdefault(right, []).append((left, weight))


def route_graph(board, net):
    graph = {}
    for item in board.GetTracks():
        if item.GetNetname() != net:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            stack = list(COPPER_LAYERS)
            left, right = stack.index(item.TopLayer()), stack.index(item.BottomLayer())
            layers = stack[min(left, right):max(left, right) + 1]
            nodes = [(layer, point(item.GetPosition())) for layer in layers]
            for left, right in zip(nodes, nodes[1:]):
                add_edge(graph, left, right, 0.0)
        elif isinstance(item, pcbnew.PCB_TRACK):
            layer = item.GetLayer()
            add_edge(graph, (layer, point(item.GetStart())),
                     (layer, point(item.GetEnd())), pcbnew.ToMM(item.GetLength()))
    return graph


def shortest(graph, starts, ends):
    ends = set(ends)
    queue = [(0.0, node) for node in starts]
    heapq.heapify(queue)
    seen = set()
    while queue:
        distance, node = heapq.heappop(queue)
        if node in seen:
            continue
        seen.add(node)
        if node in ends:
            return distance
        for neighbor, weight in graph.get(node, []):
            if neighbor not in seen:
                heapq.heappush(queue, (distance + weight, neighbor))
    raise ValueError("no routed copper path")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))
    issues, routes = [], {}

    for net, expected in POWER_ROUTES.items():
        try:
            capacitor = pad(board, *expected["capacitor"])
            if capacitor.GetNetname() != net:
                raise ValueError(f"{expected['capacitor'][0]} pad {expected['capacitor'][1]} is not {net}")
            start = [(layer, point(capacitor.GetPosition()))
                     for layer in copper_layers(board, capacitor)]
            graph = route_graph(board, net)
            values = {}
            for numbers in expected["mcu_pads"]:
                number = numbers[0]
                target = pad(board, "U1", number)
                if target.GetNetname() != net:
                    raise ValueError(f"U1 {number} is not {net}")
                ends = [(layer, point(target.GetPosition()))
                        for layer in copper_layers(board, target)]
                values[number] = shortest(graph, start, ends)
            controlled = min(values.values())
            if controlled > expected["maximum_mm"] + 1e-6:
                issues.append(f"{net} shortest capacitor-to-MCU path {controlled:.6f} mm exceeds "
                              f"released maximum {expected['maximum_mm']:.3f} mm")
            if not math.isclose(controlled, expected["released_mm"], abs_tol=1e-6):
                issues.append(f"{net} shortest capacitor-to-MCU path {controlled:.6f} mm differs "
                              f"from exact released {expected['released_mm']:.6f} mm")
            routes[net] = {
                "capacitor": f"{expected['capacitor'][0]} pad {expected['capacitor'][1]}",
                "mcu_paths_mm": {key: round(value, 6) for key, value in values.items()},
                "controlled_shortest_path_mm": round(controlled, 6),
                "released_maximum_mm": expected["maximum_mm"],
                **({"official_reference_path_mm": expected["reference_mm"]}
                   if "reference_mm" in expected else {}),
            }
        except ValueError as exc:
            issues.append(f"{net}: {exc}")

    result = {
        "board_sha256": hashlib.sha256(args.board.read_bytes()).hexdigest(),
        "routes": routes,
        "issues": issues,
        "passed": not issues,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
