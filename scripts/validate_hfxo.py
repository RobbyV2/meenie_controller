#!/usr/bin/env python3
"""Fail closed on the released nRF52840 HFXO parts, routing, and frequency budget."""

import argparse
import hashlib
import heapq
import json
import math
import pathlib

import pcbnew


COPPER_LAYERS = (pcbnew.F_Cu, 4, 6, 8, 10, 12, 14, pcbnew.B_Cu)
EXPECTED_VALUES = {
    "C1": "12pF C0G 2%",
    "C2": "12pF C0G 2%",
    "Y1": "FA-118T 32MHz CL 8pF",
}
EXPECTED_ROUTE_SHA256 = {
    "XC1": "4377f9d1c668f936b4fe35fd5f861114f33db0836f381b7fe6f279f44681a0c1",
    "XC2": "48c4bd95c32972dc041597d55e992d53a3cfed4be434b2b8eef85a0cfed24070",
}
EXPECTED_PATHS_MM = {
    "XC1": {"mcu_to_crystal": 3.520411, "mcu_to_load_capacitor": 4.367428},
    "XC2": {"mcu_to_crystal": 3.219209, "mcu_to_load_capacitor": 4.464105},
}
ENDPOINTS = {
    "XC1": {"mcu": ("U1", "B24"), "crystal": ("Y1", "1"), "capacitor": ("C1", "1")},
    "XC2": {"mcu": ("U1", "A23"), "crystal": ("Y1", "3"), "capacitor": ("C2", "1")},
}
CRYSTAL_MPN = "X1E0002510059"
CRYSTAL_LCSC = "C255986"
CRYSTAL_MANUFACTURER = "Seiko Epson"
CRYSTAL_FAMILY_DATASHEET = "https://download.epsondevice.com/td/pdf/brief/FA-118T_en.pdf"
CRYSTAL_EXACT_ORDER_REFERENCE = "https://www.epson.com.cn/ed/search/index.html"
CRYSTAL_DISTRIBUTOR_REFERENCE = "https://www.lcsc.com/product-detail/C255986.html"
CRYSTAL_PAD_GEOMETRY = {
    # Board-local coordinates after KiCad's bottom-side mirror and 90-degree rotation.
    "1": {"position_mm": (0.55, 0.40), "size_mm": (0.65, 0.55), "net": "XC1"},
    "2": {"position_mm": (-0.55, 0.40), "size_mm": (0.65, 0.55), "net": "GND"},
    "3": {"position_mm": (-0.55, -0.40), "size_mm": (0.65, 0.55), "net": "XC2"},
    "4": {"position_mm": (0.55, -0.40), "size_mm": (0.65, 0.55), "net": "GND"},
}

# Exact component limits and the conservative load estimate used by this gate.
CRYSTAL_CL_PF = 8.0
CRYSTAL_C0_PF = 0.47
CRYSTAL_CM_PF = 0.00126
CRYSTAL_ESR_MAX_OHM = 100
CRYSTAL_DRIVE_RECOMMENDED_UW = 10
CRYSTAL_DRIVE_MAX_UW = 100
CRYSTAL_INITIAL_PPM = 10
CRYSTAL_TEMPERATURE_PPM = 10
CRYSTAL_FIRST_YEAR_AGING_PPM = 1
CRYSTAL_OPERATING_TEMPERATURE_C = (-20, 75)
CAP_NOMINAL_PF = 12.0
CAP_TOLERANCE_PERCENT = 2.0
MCU_PIN_CAPACITANCE_PF = 3.0
PCB_STRAY_PER_LEG_PF = (0.5, 1.5)
MCU_ESR_LIMIT_OHM = 100
MCU_DRIVE_LIMIT_UW = 100
BLE_LIMIT_PPM = 30
OFFICIAL_REFERENCE_CRYSTAL_PATHS_MM = {"XC1": 4.464815, "XC2": 3.255138}


def point(value):
    return tuple(round(item, 6) for item in pcbnew.ToMM(value))


def footprint(board, reference):
    matches = [item for item in board.GetFootprints() if item.GetReference() == reference]
    if len(matches) != 1:
        raise ValueError(f"{reference} footprint count is {len(matches)}, not 1")
    return matches[0]


def pad(board, reference, number):
    matches = [item for item in footprint(board, reference).Pads() if item.GetNumber() == number]
    if len(matches) != 1:
        raise ValueError(f"{reference} pad {number} count is {len(matches)}, not 1")
    return matches[0]


def add_edge(graph, left, right, weight):
    graph.setdefault(left, []).append((right, weight))
    graph.setdefault(right, []).append((left, weight))


def copper_rows(board, net):
    rows = []
    for item in board.GetTracks():
        if item.GetNetname() != net:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            rows.append({
                "kind": "via",
                "position_mm": list(point(item.GetPosition())),
                "top": board.GetLayerName(item.TopLayer()),
                "bottom": board.GetLayerName(item.BottomLayer()),
                "diameter_mm": round(pcbnew.ToMM(item.GetWidth(item.TopLayer())), 6),
                "drill_mm": round(pcbnew.ToMM(item.GetDrillValue()), 6),
            })
        else:
            start, end = point(item.GetStart()), point(item.GetEnd())
            if end < start:
                start, end = end, start
            rows.append({
                "kind": "track",
                "layer": board.GetLayerName(item.GetLayer()),
                "start_mm": list(start),
                "end_mm": list(end),
                "width_mm": round(pcbnew.ToMM(item.GetWidth()), 6),
            })
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))


def route_digest(rows):
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def route_graph(board, net):
    graph = {}
    for item in board.GetTracks():
        if item.GetNetname() != net:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            top = COPPER_LAYERS.index(item.TopLayer())
            bottom = COPPER_LAYERS.index(item.BottomLayer())
            nodes = [(layer, point(item.GetPosition()))
                     for layer in COPPER_LAYERS[min(top, bottom):max(top, bottom) + 1]]
            for left, right in zip(nodes, nodes[1:]):
                add_edge(graph, left, right, 0.0)
        else:
            layer = item.GetLayer()
            add_edge(graph, (layer, point(item.GetStart())),
                     (layer, point(item.GetEnd())), pcbnew.ToMM(item.GetLength()))
    return graph


def pad_nodes(board, item, graph):
    layers = {layer for layer in COPPER_LAYERS if item.IsOnLayer(layer)}
    center = point(item.GetPosition())
    exact = [(layer, center) for layer in layers if (layer, center) in graph]
    if exact:
        return exact
    size_x, size_y = pcbnew.ToMM(item.GetSize())
    # Fall back to any anchor inside the pad only when no center anchor exists.
    # The exact net digest prevents this tolerance from masking a route change.
    radius = math.hypot(size_x, size_y) / 2 + 0.002
    nodes = [node for node in graph
             if node[0] in layers and math.dist(node[1], center) <= radius]
    if not nodes:
        raise ValueError("pad has no routed copper anchor")
    return nodes


def shortest(graph, starts, ends):
    end_set = set(ends)
    queue = [(0.0, node) for node in starts]
    heapq.heapify(queue)
    seen = set()
    while queue:
        distance, node = heapq.heappop(queue)
        if node in seen:
            continue
        seen.add(node)
        if node in end_set:
            return distance
        for neighbor, weight in graph.get(node, []):
            if neighbor not in seen:
                heapq.heappush(queue, (distance + weight, neighbor))
    raise ValueError("no routed copper path")


def parallel_frequency_factor(load_pf):
    return math.sqrt(1.0 + CRYSTAL_CM_PF / (CRYSTAL_C0_PF + load_pf))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))
    issues = []

    observed_values = {}
    for reference, expected in EXPECTED_VALUES.items():
        try:
            observed = footprint(board, reference).GetValue()
            observed_values[reference] = observed
            if observed != expected:
                issues.append(f"{reference} value {observed!r} differs from exact released {expected!r}")
        except ValueError as exc:
            issues.append(str(exc))

    crystal_land = {}
    try:
        crystal = footprint(board, "Y1")
        expected_fields = {
            "MPN": CRYSTAL_MPN,
            "LCSC Part": CRYSTAL_LCSC,
            "Manufacturer": CRYSTAL_MANUFACTURER,
            "Datasheet": CRYSTAL_FAMILY_DATASHEET,
        }
        for name, expected in expected_fields.items():
            try:
                observed = crystal.GetFieldText(name)
            except KeyError:
                observed = ""
            if observed != expected:
                issues.append(f"Y1 {name} {observed!r} differs from exact released {expected!r}")
        origin = pcbnew.ToMM(crystal.GetPosition())
        angle = math.radians(crystal.GetOrientationDegrees())
        cosine, sine = math.cos(angle), math.sin(angle)
        for number, expected in CRYSTAL_PAD_GEOMETRY.items():
            item = pad(board, "Y1", number)
            absolute = pcbnew.ToMM(item.GetPosition())
            dx, dy = absolute[0] - origin[0], absolute[1] - origin[1]
            local = (round(dx * cosine + dy * sine, 6),
                     round(-dx * sine + dy * cosine, 6))
            size = tuple(round(value, 6) for value in pcbnew.ToMM(item.GetSize()))
            observed = {"position_mm": local, "size_mm": size, "net": item.GetNetname()}
            crystal_land[number] = {
                "position_mm": list(local), "size_mm": list(size), "net": item.GetNetname()
            }
            if observed != expected:
                issues.append(f"Y1 pad {number} {observed!r} differs from exact released {expected!r}")
    except ValueError as exc:
        issues.append(str(exc))

    nets = {}
    for net, endpoints in ENDPOINTS.items():
        try:
            graph = route_graph(board, net)
            rows = copper_rows(board, net)
            digest = route_digest(rows)
            if digest != EXPECTED_ROUTE_SHA256[net]:
                issues.append(f"{net} copper SHA-256 {digest} differs from exact released "
                              f"{EXPECTED_ROUTE_SHA256[net]}")
            endpoint_pads = {name: pad(board, *identifier) for name, identifier in endpoints.items()}
            for name, item in endpoint_pads.items():
                if item.GetNetname() != net:
                    issues.append(f"{endpoints[name][0]} pad {endpoints[name][1]} is {item.GetNetname()}, not {net}")
            starts = pad_nodes(board, endpoint_pads["mcu"], graph)
            measured = {
                "mcu_to_crystal": shortest(graph, starts, pad_nodes(board, endpoint_pads["crystal"], graph)),
                "mcu_to_load_capacitor": shortest(graph, starts, pad_nodes(board, endpoint_pads["capacitor"], graph)),
            }
            for name, expected in EXPECTED_PATHS_MM[net].items():
                if not math.isclose(measured[name], expected, abs_tol=1e-6):
                    issues.append(f"{net} {name} {measured[name]:.6f} mm differs from exact released "
                                  f"{expected:.6f} mm")
            if measured["mcu_to_crystal"] > OFFICIAL_REFERENCE_CRYSTAL_PATHS_MM[net] + 1e-6:
                issues.append(f"{net} MCU-to-crystal path exceeds Nordic configuration-1 reference")
            nets[net] = {
                "copper_item_count": len(rows),
                "copper_sha256": digest,
                "mcu_to_crystal_mm": round(measured["mcu_to_crystal"], 6),
                "mcu_to_load_capacitor_mm": round(measured["mcu_to_load_capacitor"], 6),
                "nordic_configuration_1_mcu_to_crystal_mm": OFFICIAL_REFERENCE_CRYSTAL_PATHS_MM[net],
            }
        except (ValueError, IndexError) as exc:
            issues.append(f"{net}: {exc}")

    cap_min = CAP_NOMINAL_PF * (1 - CAP_TOLERANCE_PERCENT / 100)
    cap_max = CAP_NOMINAL_PF * (1 + CAP_TOLERANCE_PERCENT / 100)
    load_range = (
        (cap_min + MCU_PIN_CAPACITANCE_PF + PCB_STRAY_PER_LEG_PF[0]) / 2,
        (cap_max + MCU_PIN_CAPACITANCE_PF + PCB_STRAY_PER_LEG_PF[1]) / 2,
    )
    reference_factor = parallel_frequency_factor(CRYSTAL_CL_PF)
    pull_range = tuple((parallel_frequency_factor(value) / reference_factor - 1) * 1e6
                       for value in load_range)
    worst_load_pull = max(abs(value) for value in pull_range)
    total_budget = (CRYSTAL_INITIAL_PPM + CRYSTAL_TEMPERATURE_PPM +
                    CRYSTAL_FIRST_YEAR_AGING_PPM + worst_load_pull)
    if not (load_range[0] <= CRYSTAL_CL_PF <= load_range[1]):
        issues.append(f"estimated load range {load_range} pF does not contain crystal CL {CRYSTAL_CL_PF} pF")
    if total_budget > BLE_LIMIT_PPM:
        issues.append(f"conservative HFXO budget {total_budget:.3f} ppm exceeds BLE limit {BLE_LIMIT_PPM} ppm")
    if CRYSTAL_ESR_MAX_OHM > MCU_ESR_LIMIT_OHM:
        issues.append("crystal ESR maximum exceeds nRF52840 HFXO limit")
    if CRYSTAL_DRIVE_MAX_UW < MCU_DRIVE_LIMIT_UW:
        issues.append("crystal maximum drive is below nRF52840 HFXO drive limit")

    result = {
        "board_sha256": hashlib.sha256(args.board.read_bytes()).hexdigest(),
        "parts": {
            "crystal": {
                "reference": "Y1", "mpn": CRYSTAL_MPN, "frequency_mhz": 32,
                "load_capacitance_pf": CRYSTAL_CL_PF, "shunt_capacitance_pf": CRYSTAL_C0_PF,
                "motional_capacitance_pf": CRYSTAL_CM_PF,
                "esr_max_ohm": CRYSTAL_ESR_MAX_OHM,
                "drive_recommended_uw": CRYSTAL_DRIVE_RECOMMENDED_UW,
                "drive_max_uw": CRYSTAL_DRIVE_MAX_UW,
                "initial_tolerance_ppm": CRYSTAL_INITIAL_PPM,
                "temperature_stability_ppm": CRYSTAL_TEMPERATURE_PPM,
                "first_year_aging_ppm": CRYSTAL_FIRST_YEAR_AGING_PPM,
                "operating_temperature_c": list(CRYSTAL_OPERATING_TEMPERATURE_C),
                "exact_order_reference": CRYSTAL_EXACT_ORDER_REFERENCE,
                "family_datasheet": CRYSTAL_FAMILY_DATASHEET,
                "distributor_reference": CRYSTAL_DISTRIBUTOR_REFERENCE,
            },
            "load_capacitors": {
                "references": ["C1", "C2"], "mpn": "GRM0335C1H120GA01D",
                "nominal_pf": CAP_NOMINAL_PF, "tolerance_percent": CAP_TOLERANCE_PERCENT,
                "dielectric": "C0G/NP0",
            },
            "observed_values": observed_values,
            "crystal_land_pattern": crystal_land,
        },
        "routing": nets,
        "frequency_budget": {
            "nrf52840_ble_limit_ppm": BLE_LIMIT_PPM,
            "nrf52840_pin_capacitance_pf_typical": MCU_PIN_CAPACITANCE_PF,
            "estimated_pcb_stray_per_leg_pf": list(PCB_STRAY_PER_LEG_PF),
            "estimated_total_load_range_pf": [round(value, 3) for value in load_range],
            "calculated_load_pull_range_ppm": [round(value, 3) for value in pull_range],
            "conservative_total_first_year_temperature_budget_ppm": round(total_budget, 3),
            "remaining_ble_margin_ppm": round(BLE_LIMIT_PPM - total_budget, 3),
        },
        "zero_margin_component_limits": {
            "crystal_esr_max_equals_mcu_limit": CRYSTAL_ESR_MAX_OHM == MCU_ESR_LIMIT_OHM,
            "crystal_drive_max_equals_mcu_limit": CRYSTAL_DRIVE_MAX_UW == MCU_DRIVE_LIMIT_UW,
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
