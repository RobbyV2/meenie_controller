#!/usr/bin/env python3
"""Validate the release-critical nRF52840 circuit from KiCad's XML netlist."""

import argparse
import json
import pathlib
import xml.etree.ElementTree as ET


EXPECTED_NETS = {
    "+3V3": {
        ("C3", "1"), ("C4", "1"), ("C6", "1"), ("C8", "1"), ("C12", "1"),
        ("U1", "A22"), ("U1", "AD14"), ("U1", "AD23"), ("U1", "B1"),
        ("U1", "W1"), ("W8", "1"),
    },
    "VBUS": {
        ("C11", "1"), ("D2", "1"), ("J1", "A4B9"), ("J1", "B4A9"),
        ("R1", "1"),
    },
    "VBUS_MCU": {
        ("C19", "1"), ("R1", "2"), ("U1", "AD2"), ("U1", "Y2"),
    },
    "DEC1": {("C18", "1"), ("U1", "C1")},
    "DEC3": {("C5", "1"), ("U1", "D23")},
    "DEC4_6": {("C7", "1"), ("U1", "B5"), ("U1", "E24")},
    "DEC5": {("C21", "1"), ("U1", "N24")},
    "DECUSB": {("C9", "1"), ("U1", "AC5")},
    "CC1": {("J1", "A5"), ("R2", "1")},
    "CC2": {("J1", "B5"), ("R3", "1")},
    "LED_A": {("D3", "2"), ("R4", "2")},
    "P1.02": {("R4", "1"), ("U1", "W24")},
    "USB_D+": {("D1", "1"), ("J1", "A6"), ("J1", "B6"), ("U1", "AD6")},
    "USB_D-": {("D4", "2"), ("J1", "A7"), ("J1", "B7"), ("U1", "AD4")},
    "XC1": {("C1", "1"), ("U1", "B24"), ("Y1", "1")},
    "XC2": {("C2", "1"), ("U1", "A23"), ("Y1", "3")},
    "RF_CHIP": {("C14", "1"), ("L1", "1"), ("U1", "H23")},
    "RF_50": {("C15", "1"), ("C16", "1"), ("L1", "2"), ("L2", "1")},
    "ANT_FEED": {("AE1", "1"), ("C17", "1"), ("L2", "2")},
    "nRESET": {("SW1", "1"), ("U1", "AC13")},
    "SWDIO": {("S1", "1"), ("U1", "AC24")},
    "SWCLK": {("S2", "1"), ("U1", "AA24")},
}

GPIO_BREAKOUTS = {
    "P0.02": ("E3", "A12"),
    "P0.03": ("W6", "B13"),
    "P1.00": ("W1", "AD22"),
    "P1.01": ("E4", "Y23"),
    "P1.03": ("E6", "V23"),
    "P1.04": ("W7", "U24"),
    "P1.05": ("E2", "T23"),
    "P1.06": ("E5", "R24"),
    "P1.07": ("W9", "P23"),
    "P1.10": ("W5", "A20"),
    "P1.11": ("W2", "B19"),
    "P1.12": ("E9", "B17"),
    "P1.13": ("W4", "A16"),
    "P1.14": ("W3", "B15"),
    "P1.15": ("E8", "A14"),
}

EXPECTED_VALUES = {
    "C1": "12pF C0G 2%", "C2": "12pF C0G 2%", "C3": "100nF",
    "C4": "100nF", "C5": "100pF", "C6": "4.7uF", "C7": "1.0uF",
    "C8": "100nF", "C9": "4.7uF", "C11": "4.7uF", "C12": "100nF",
    "C18": "100nF", "C19": "4.7uF", "C21": "820pF C0G 5%", "R1": "2R2",
    "R2": "5.1k", "R3": "5.1k", "R4": "1k",
    "D1": "TPD1E01B04DPLR", "D2": "ESD441DPLR", "D3": "red", "D4": "ESD5311Z-2/TR",
    "C14": "0.8pF", "C15": "0.5pF", "C16": "0.8pF", "C17": "DNP",
    "L1": "4.7nH", "L2": "4.7nH",
    "U1": "nRF52840-QIAA-R",
    "Y1": "FA-118T 32MHz CL 8pF",
}

EXPECTED_FOOTPRINTS = {
    "D1": "meenie:ESD_X2SON_DPL",
    "D2": "meenie:ESD_X2SON_DPL",
    "D3": "meenie:LED0402-R-RD_RED",
    "D4": "meenie:ESD_DFN0603-2",
    "J1": "meenie:USB-TYPE-C-SMD_TYPE-C-16P-QTWT",
    "C14": "meenie:C0201", "C15": "meenie:C0201",
    "C16": "meenie:C0201", "C17": "meenie:C0201",
    "L1": "meenie:L0201", "L2": "meenie:L0201",
    "SW1": "meenie:Switch_NanoT_AS",
    "U1": "meenie:AQFN-73_L7.0-W7.0-P0.50-BL-EP4.8",
    "Y1": "meenie:Crystal_SMD_1612-4Pin_FA-118T",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--netlist", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    root = ET.parse(args.netlist).getroot()
    issues = []
    nets = {}
    functions = {}
    for net in root.findall("./nets/net"):
        name = net.get("name")
        nets[name] = {(node.get("ref"), node.get("pin")) for node in net.findall("node")}
        functions[name] = {
            (node.get("ref"), node.get("pin")): node.get("pinfunction", "")
            for node in net.findall("node")
        }
    components = {comp.get("ref"): comp for comp in root.findall("./components/comp")}
    values = {ref: comp.findtext("value", "") for ref, comp in components.items()}

    for name, expected in EXPECTED_NETS.items():
        actual = nets.get(name)
        if actual != expected:
            issues.append(f"net {name} nodes {sorted(actual or set())!r} != {sorted(expected)!r}")
    for name, (breakout, ball) in GPIO_BREAKOUTS.items():
        expected = {(breakout, "1"), ("U1", ball)}
        actual = nets.get(name)
        if actual != expected:
            issues.append(f"GPIO {name} nodes {sorted(actual or set())!r} != {sorted(expected)!r}")
            continue
        pinfunction = functions[name].get(("U1", ball), "")
        if not pinfunction.startswith(name.replace(".", ".")):
            issues.append(f"GPIO {name} reaches U1 {ball} function {pinfunction!r}")
    for ref, expected in EXPECTED_VALUES.items():
        if values.get(ref) != expected:
            issues.append(f"{ref} value {values.get(ref)!r} != {expected!r}")
    for ref, expected in EXPECTED_FOOTPRINTS.items():
        actual = components.get(ref).findtext("footprint", "") if ref in components else None
        if actual != expected:
            issues.append(f"{ref} footprint {actual!r} != {expected!r}")

    required_ground = {
        ("D1", "2"), ("D2", "2"), ("D3", "1"), ("D4", "1"),
        ("J1", "0"), ("J1", "A1B12"), ("J1", "B1A12"),
        ("R2", "2"), ("R3", "2"), ("SW1", "2"),
        ("U1", "0"), ("U1", "B7"), ("U1", "F23"),
        ("Y1", "2"), ("Y1", "4"),
    }
    missing_ground = required_ground - nets.get("GND", set())
    if missing_ground:
        issues.append(f"GND lacks required nodes {sorted(missing_ground)!r}")

    no_connect = {node for name, nodes in nets.items() if name.startswith("unconnected-(") for node in nodes}
    required_nc = {("U1", "A18"), ("U1", "B3"), ("U1", "AB2")}
    if not required_nc <= no_connect:
        issues.append(f"configuration-1 no-connects lack {sorted(required_nc - no_connect)!r}")

    result = {
        "passed": not issues,
        "issues": issues,
        "critical_net_count": len(EXPECTED_NETS),
        "gpio_breakout_count": len(GPIO_BREAKOUTS),
        "gpio_breakouts": GPIO_BREAKOUTS,
        "power_mode": {
            "nrf52840_qiaa_reference_configuration": 1,
            "nordic_product_specification_version": 1.9,
            "nordic_reference_bom_version": 1.1,
            "vddh_source": "USB_VBUS",
            "regulator_mode": "LDO",
            "uicr_regout0_required_v": 3.3,
            "uicr_regout0_erased_v": 1.8,
            "uicr_pselreset_pin": "P0.18",
            "uicr_pselreset_register_count": 2,
            "dcdc_enabled": False,
            "vdd_bulk": {"reference": "C6", "mpn": "GRM188R61E475KE11D",
                         "nominal_uf": 4.7, "rated_v": 25, "dielectric": "X5R", "package": "0603"},
            "vdd_bypass": {"nominal_uf": 0.1, "quantity": 4},
            "decusb": {"reference": "C9", "mpn": "GRM188R61E475KE11D", "nwp_030": True},
            "effective_capacitance_minimum_uf": {"VDD": 2.7, "DECUSB": 2.35},
        },
        "protection_and_polarity": {
            "D1": {"mpn": "TPD1E01B04DPLR", "bidirectional": True,
                   "pin_1_net": "USB_D+", "pin_2_net": "GND"},
            "D2": {"mpn": "ESD441DPLR", "pin_1_net": "VBUS", "pin_2_net": "GND"},
            "D3": {"mpn": "LTST-C281KRKT", "cathode_pin": 1, "cathode_net": "GND",
                   "anode_pin": 2, "anode_net": "LED_A"},
            "D4": {"mpn": "ESD5311Z-2/TR", "bidirectional": True,
                   "pin_1_net": "GND", "pin_2_net": "USB_D-"},
        },
    }
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
