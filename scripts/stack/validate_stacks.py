#!/usr/bin/env python3
"""Fail-closed validator for Meenie HDI stack manifests (schema 4)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

TOL = 1e-6
ACTIVE_MARKER_TYPES = ("clearance", "hole_clearance", "holes_co_located")
VIA_PHYSICAL_CLEARANCE_RULES = {
    "different_nets": ("via physical copper clearance, different nets", 0.075),
}
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu")
REQUIRED_RULE_SNIPPETS = {
    "PTH hole to copper": ("(constraint hole_clearance (min 0.28mm))", "A.Pad_Type == 'Through-hole'"),
    "PTH hole to PTH hole": ("(constraint hole_to_hole (min 0.45mm))",),
    "RF corridor coplanar setback": ("(constraint clearance (min 0.22mm))", "(layer outer)"),
    "U1 north pocket crystal and decoupling escape": ("(constraint clearance (min 0.09mm))", "insideArea('U1 north pocket')"),
    "NextPCB Advanced HDI copper clearance": ("(constraint clearance (min 0.08mm))",),
    "NextPCB Advanced HDI laser hole clearance": ("(constraint hole_clearance (min 0.08mm))", "A.Via_Type == 'Micro'"),
    "NextPCB mechanical via annular ring": ("(constraint annular_width (min 0.09mm))", "A.Via_Type != 'Micro'"),
    "NextPCB HDI RF_50 B.Cu width": ("(constraint track_width (min 0.17mm) (max 0.17mm))", "A.Layer == 'B.Cu'"),
    "NextPCB HDI RF_50 same-side GND setback": ("(constraint clearance (min 0.300mm))", "insideArea('RF corridor, RF_50')"),
}


def balanced_blocks(text: str, token: str) -> list[str]:
    blocks: list[str] = []
    cursor = 0
    while True:
        start = text.find(token, cursor)
        if start < 0:
            return blocks
        depth = 0
        quoted = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : index + 1])
                    cursor = index + 1
                    break
        else:
            raise ValueError(f"unbalanced block beginning {token!r}")


def parse_pair(block: str, field: str) -> tuple[str, str]:
    match = re.search(rf"\({re.escape(field)}\s+\"([^\"]+)\"\s+\"([^\"]+)\"\)", block)
    if not match:
        raise ValueError(f"missing {field} pair in via")
    return match.group(1), match.group(2)


def parse_float(block: str, field: str) -> float:
    match = re.search(rf"\({re.escape(field)}\s+([-+0-9.eE]+)", block)
    if not match:
        raise ValueError(f"missing {field} in via")
    return float(match.group(1))


def parse_at(block: str) -> tuple[float, float]:
    match = re.search(r"\(at\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", block)
    if not match:
        raise ValueError("missing at in via")
    return float(match.group(1)), float(match.group(2))


def parse_raw_vias(board_text: str) -> list[dict]:
    vias: list[dict] = []
    # Top-level board vias start with exactly one tab.  The leading newline also
    # keeps this from mistaking setup's nested ``(vias not_allowed)`` token for
    # a copper via.
    for block in balanced_blocks(board_text, "\n\t(via"):
        uuid_match = re.search(r"\(uuid\s+\"([^\"]+)\"\)", block)
        net_name_match = re.search(r"\(net\s+\"([^\"]*)\"\)", block)
        net_id_match = re.search(r"\(net\s+(\d+)\)", block)
        if not uuid_match or (not net_name_match and not net_id_match):
            raise ValueError("via missing uuid or net")
        kind_match = re.match(r"\(via(?:\s+(micro|blind|buried))?", block.lstrip())
        vias.append(
            {
                "uuid": uuid_match.group(1),
                "net_id": int(net_id_match.group(1)) if net_id_match else None,
                "net": net_name_match.group(1) if net_name_match else None,
                "kind": (kind_match.group(1) if kind_match and kind_match.group(1) else "through"),
                "at": parse_at(block),
                "size": parse_float(block, "size"),
                "drill": parse_float(block, "drill"),
                "layers": parse_pair(block, "layers"),
                "block": block,
            }
        )
    net_names = {
        int(number): name
        for number, name in re.findall(r"\(net\s+(\d+)\s+\"([^\"]*)\"\)", board_text)
    }
    for via in vias:
        if via["net"] is None:
            via["net"] = net_names.get(via["net_id"], f"<net:{via['net_id']}>")
    return vias


def parse_layer_stack(board_text: str) -> dict:
    stack_blocks = balanced_blocks(board_text, "\n\t\t(stackup")
    if len(stack_blocks) != 1:
        raise ValueError(f"expected one board stackup, found {len(stack_blocks)}")
    rows = []
    for block in balanced_blocks(stack_blocks[0], "\n\t\t\t(layer"):
        name_match = re.match(r'\(layer\s+"([^"]+)"', block.lstrip())
        kind_match = re.search(r'\(type\s+"([^"]+)"\)', block)
        thickness_match = re.search(r"\(thickness\s+([-+0-9.eE]+)\)", block)
        if not name_match or not kind_match:
            continue
        name, kind = name_match.group(1), kind_match.group(1)
        if kind != "copper" and not name.startswith("dielectric "):
            continue
        if not thickness_match:
            raise ValueError(f"stack layer {name} lacks thickness")
        material_match = re.search(r'\(material\s+"([^"]*)"\)', block)
        rows.append({
            "name": name,
            "type": kind,
            "thickness_mm": float(thickness_match.group(1)),
            "material": material_match.group(1) if material_match else "",
        })
    general_blocks = balanced_blocks(board_text, "\n\t(general")
    if len(general_blocks) != 1:
        raise ValueError(f"expected one board general block, found {len(general_blocks)}")
    declared = parse_float(general_blocks[0], "thickness")
    total = sum(row["thickness_mm"] for row in rows)
    copper = [row["name"] for row in rows if row["type"] == "copper"]
    expected = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"]
    if len(rows) != 15 or copper != expected:
        raise ValueError(f"unexpected 8L stack rows/copper: {len(rows)} {copper!r}")
    if not math.isclose(total, declared, abs_tol=TOL):
        raise ValueError(f"stack row sum {total:.6f} differs from board thickness {declared:.6f}")
    return {"finished_thickness_mm": declared, "rows": rows}


def drc_signature(violation: dict) -> tuple[str, float, float, tuple[str, ...]]:
    items = violation.get("items", [])
    if not items:
        raise ValueError("DRC violation has no items")
    pos = items[0].get("pos")
    if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
        raise ValueError("DRC violation item has no position")
    return (
        violation.get("type", ""),
        round(float(pos["x"]), 6),
        round(float(pos["y"]), 6),
        tuple(sorted(item.get("uuid", "") for item in items)),
    )


def marker_rule(description: str) -> str:
    match = re.search(r"\((.+?) (?:hole )?clearance [\d.]+ mm", description)
    return match.group(1) if match else description


def via_physical_clearance(rules_text: str) -> dict:
    found: dict[str, float] = {}
    for key, (name, _) in VIA_PHYSICAL_CLEARANCE_RULES.items():
        block = re.search(
            rf'\(rule "{re.escape(name)}"(.*?)\(condition "([^"]*)"\)\)',
            rules_text,
            flags=re.S,
        )
        if not block:
            raise ValueError(f"rules file lacks the {name!r} physical-clearance rule")
        minimum = re.search(r"\(constraint physical_clearance \(min ([\d.]+)mm\)\)", block.group(1))
        if not minimum:
            raise ValueError(f"{name!r} does not carry a physical_clearance minimum")
        if "A.Type == 'Via' && B.Type == 'Via'" not in block.group(2):
            raise ValueError(f"{name!r} is not scoped to a via pair")
        found[key] = float(minimum.group(1))
    return found


def via_spacing_metrics(vias: list[dict]) -> dict:
    def layers(via: dict) -> set[str]:
        start, end = (COPPER_LAYERS.index(name) for name in via["layers"])
        return set(COPPER_LAYERS[min(start, end):max(start, end) + 1])

    candidates = {"center": [], "copper_edge": [], "hole_edge": []}
    for index, left in enumerate(vias):
        left_layers = layers(left)
        for right in vias[index + 1:]:
            common_layers = left_layers & layers(right)
            if left["net"] == right["net"] or not common_layers:
                continue
            center = math.dist(left["at"], right["at"])
            values = {
                "center": center,
                "copper_edge": center - (left["size"] + right["size"]) / 2.0,
                "hole_edge": center - (left["drill"] + right["drill"]) / 2.0,
            }
            uuids = sorted((left["uuid"], right["uuid"]))
            for key, value in values.items():
                candidates[key].append((round(value, 6), uuids, sorted(common_layers)))
    result = {}
    for key, rows in candidates.items():
        if not rows:
            raise ValueError("board has no common-layer different-net via pairs")
        value, uuids, common_layers = min(rows)
        result[f"minimum_{key}_mm"] = {
            "value": value,
            "via_uuids": uuids,
            "common_layers": common_layers,
        }
    return result


def validate_required_rules(rules_text: str) -> list[str]:
    issues = []
    for name, snippets in REQUIRED_RULE_SNIPPETS.items():
        blocks = balanced_blocks(rules_text, f'(rule "{name}"')
        if len(blocks) != 1:
            issues.append(f"rules file has {len(blocks)} copies of required rule {name!r}")
            continue
        compact = re.sub(r"\s+", " ", blocks[0])
        for snippet in snippets + ("(severity error)",):
            if re.sub(r"\s+", " ", snippet) not in compact:
                issues.append(f"required rule {name!r} lacks exact {snippet!r}")
    return issues


def manifest_signature(marker: dict) -> tuple[str, float, float, tuple[str, ...]]:
    return (
        marker["type"],
        round(float(marker["position_mm"][0]), 6),
        round(float(marker["position_mm"][1]), 6),
        tuple(sorted(marker["via_uuids"])),
    )


def project_exclusion_signature(exclusion: object) -> tuple[str, float, float, tuple[str, ...]]:
    if isinstance(exclusion, str):
        key = exclusion
    elif isinstance(exclusion, list) and exclusion and isinstance(exclusion[0], str):
        key = exclusion[0]
    else:
        raise ValueError(f"unsupported project exclusion record {exclusion!r}")
    fields = key.split("|")
    if len(fields) != 5:
        raise ValueError(f"unsupported project exclusion key {key!r}")
    marker_type, x_nm, y_nm, uuid_a, uuid_b = fields
    return (
        marker_type,
        round(int(x_nm) / 1_000_000.0, 6),
        round(int(y_nm) / 1_000_000.0, 6),
        tuple(sorted((uuid_a, uuid_b))),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate(board_dir: Path, manifest_path: Path, drc_path: Path | None) -> list[str]:
    errors: list[str] = []
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 4:
        return [f"manifest schema must be 4, got {manifest.get('schema')!r}"]

    required = ("board", "project", "rules", "source_hashes", "layer_stack",
                "via_physical_clearance_rule_mm", "via_spacing_observed_mm",
                "allowed_layer_sequences", "stacks")
    for field in required:
        if field not in manifest:
            fail(errors, f"manifest missing {field}")
    if errors:
        return errors

    board_path = board_dir / manifest["board"]
    project_path = board_dir / manifest["project"]
    rules_path = board_dir / manifest["rules"]
    for path in (board_path, project_path, rules_path):
        if not path.is_file():
            fail(errors, f"missing required project file {path}")
    if errors:
        return errors

    for key, path in (("board_sha256", board_path), ("project_sha256", project_path), ("rules_sha256", rules_path)):
        expected = manifest["source_hashes"].get(key)
        actual = sha256(path)
        if expected != actual:
            fail(errors, f"{key} mismatch: manifest {expected}, actual {actual}")

    board_text = board_path.read_text()
    rules_text = rules_path.read_text()
    project = json.loads(project_path.read_text())
    actual_layer_stack = parse_layer_stack(board_text)
    if manifest["layer_stack"] != actual_layer_stack:
        fail(errors, f"layer stack differs from exact manifest: {actual_layer_stack!r}")
    try:
        all_vias = parse_raw_vias(board_text)
    except ValueError as exc:
        return errors + [str(exc)]
    microvias = [via for via in all_vias if via["kind"] == "micro"]
    micro_by_uuid = {via["uuid"]: via for via in microvias}
    if len(micro_by_uuid) != len(microvias):
        fail(errors, "board has duplicate microvia UUIDs")
    for via in microvias:
        if not math.isclose(via["size"], 0.25, abs_tol=TOL) or not math.isclose(via["drill"], 0.15, abs_tol=TOL):
            fail(errors, f"microvia geometry {via['uuid']} is {via['size']}/{via['drill']}mm, approved 0.25/0.15mm")
    for via in (item for item in all_vias if item["kind"] != "micro"):
        ring = (via["size"] - via["drill"]) / 2.0
        if ring < 0.09 - TOL:
            fail(errors, f"mechanical via annular ring {via['uuid']} is {ring:.6f}mm, minimum 0.09mm")

    central = [via for via in all_vias if via["kind"] in ("blind", "buried")]
    central_manifest = manifest.get("central_buried_transitions", [])
    central_by_uuid = {via["uuid"]: via for via in central}
    central_owners = set()
    for item in central_manifest:
        uuid = item.get("via_uuid")
        if uuid in central_owners:
            fail(errors, f"central transition {uuid} appears more than once")
            continue
        central_owners.add(uuid)
        actual = central_by_uuid.get(uuid)
        if actual is None:
            fail(errors, f"manifest central transition {uuid!r} is not a board blind/buried via")
            continue
        expected_layers = tuple(item.get("layers", []))
        if expected_layers != ("In3.Cu", "In4.Cu"):
            fail(errors, f"central transition {uuid}: illegal pair {expected_layers}")
        for field in ("net", "layers"):
            expected = item.get(field)
            actual_value = actual[field]
            if field == "layers":
                expected = tuple(expected)
            if actual_value != expected:
                fail(errors, f"central transition {uuid}: {field} {actual_value!r}, manifest {expected!r}")
        center = item.get("center_mm", [])
        if len(center) != 2 or any(not math.isclose(actual["at"][i], float(center[i]), abs_tol=TOL) for i in (0, 1)):
            fail(errors, f"central transition {uuid}: center {actual['at']}, manifest {center}")
        for key, field in (("size", "pad_mm"), ("drill", "hole_mm")):
            if not math.isclose(actual[key], float(item.get(field, -1)), abs_tol=TOL):
                fail(errors, f"central transition {uuid}: {field} {actual[key]}, manifest {item.get(field)}")
    for uuid in sorted(set(central_by_uuid) - central_owners):
        fail(errors, f"unmanifested board blind/buried via {uuid}")
    for uuid in sorted(central_owners - set(central_by_uuid)):
        fail(errors, f"manifest references missing blind/buried via {uuid}")

    try:
        separation = via_physical_clearance(rules_text)
    except ValueError as exc:
        separation = {}
        fail(errors, str(exc))
    for key, (name, expected) in VIA_PHYSICAL_CLEARANCE_RULES.items():
        if key in separation and not math.isclose(separation[key], expected, abs_tol=TOL):
            fail(errors, f"{name!r} minimum is {separation[key]}mm, approved {expected}mm")
        declared = manifest.get("via_physical_clearance_rule_mm", {}).get(key)
        if declared is None or not math.isclose(float(declared), expected, abs_tol=TOL):
            fail(errors, f"manifest via_physical_clearance_rule_mm[{key}] is {declared!r}, approved {expected}mm")
    observed_spacing = via_spacing_metrics(all_vias)
    if manifest.get("via_spacing_observed_mm") != observed_spacing:
        fail(errors, f"manifest via_spacing_observed_mm differs from board: {observed_spacing!r}")
    if re.search(r"\(severity\s+ignore\)", rules_text, flags=re.I):
        fail(errors, "rules file contains a broad severity-ignore rule")
    errors.extend(validate_required_rules(rules_text))

    canonical: dict[str, list[tuple[str, str]]] = {}
    for face, pairs in manifest["allowed_layer_sequences"].items():
        canonical[face] = [tuple(pair) for pair in pairs]
    expected_canonical = {
        "top": [("F.Cu", "In1.Cu"), ("In1.Cu", "In2.Cu"), ("In2.Cu", "In3.Cu")],
        "bottom": [("In4.Cu", "In5.Cu"), ("In5.Cu", "In6.Cu"), ("In6.Cu", "B.Cu")],
    }
    if canonical != expected_canonical:
        fail(errors, f"allowed layer sequences do not match approved adjacent build-up: {canonical}")
    allowed_tier_counts = manifest.get("allowed_tier_counts")
    if allowed_tier_counts != [1, 2, 3]:
        fail(errors, f"allowed_tier_counts must be [1, 2, 3], got {allowed_tier_counts!r}")

    owners: dict[str, tuple[str, int]] = {}
    for stack in manifest["stacks"]:
        name = stack.get("name", "<unnamed>")
        net = stack.get("net")
        face = stack.get("face")
        tiers = stack.get("tiers", [])
        if face not in canonical:
            fail(errors, f"{name}: invalid face {face!r}")
            continue
        if len(tiers) not in (1, 2, 3):
            fail(errors, f"{name}: tier count {len(tiers)} is not 1, 2, or 3")
        indices: list[int] = []
        for index, tier in enumerate(tiers):
            uuid = tier.get("via_uuid")
            if uuid in owners:
                fail(errors, f"microvia {uuid} appears in both {owners[uuid][0]} and {name}")
                continue
            owners[uuid] = (name, index)
            actual = micro_by_uuid.get(uuid)
            if actual is None:
                fail(errors, f"{name} tier {index}: UUID {uuid!r} is not a board microvia")
                continue
            expected_layers = tuple(tier.get("layers", []))
            if expected_layers not in canonical[face]:
                fail(errors, f"{name} tier {index}: illegal adjacent pair {expected_layers}")
            else:
                indices.append(canonical[face].index(expected_layers))
            if actual["net"] != net:
                fail(errors, f"{name} tier {index}: net {actual['net']!r}, manifest {net!r}")
            if actual["layers"] != expected_layers:
                fail(errors, f"{name} tier {index}: layers {actual['layers']}, manifest {expected_layers}")
            center = tier.get("center_mm", [])
            if len(center) != 2 or any(not math.isclose(actual["at"][i], float(center[i]), abs_tol=TOL) for i in (0, 1)):
                fail(errors, f"{name} tier {index}: center {actual['at']}, manifest {center}")
            if not math.isclose(actual["size"], float(tier.get("pad_mm", -1)), abs_tol=TOL):
                fail(errors, f"{name} tier {index}: pad {actual['size']}, manifest {tier.get('pad_mm')}")
            if not math.isclose(actual["drill"], float(tier.get("hole_mm", -1)), abs_tol=TOL):
                fail(errors, f"{name} tier {index}: drill {actual['drill']}, manifest {tier.get('hole_mm')}")

        if indices and indices != list(range(indices[0], indices[0] + len(indices))):
            fail(errors, f"{name}: tiers are not one contiguous ordered subsequence: {indices}")
        for left, right in zip(tiers, tiers[1:]):
            if left.get("via_uuid") not in micro_by_uuid or right.get("via_uuid") not in micro_by_uuid:
                continue
            a = micro_by_uuid[left["via_uuid"]]
            b = micro_by_uuid[right["via_uuid"]]
            distance = math.dist(a["at"], b["at"])
            capture = (a["size"] + b["size"]) / 2.0
            if distance > capture + TOL:
                fail(errors, f"{name}: adjacent tiers do not overlap capture pads ({distance:.6f} > {capture:.6f}mm)")

    actual_uuids = set(micro_by_uuid)
    manifest_uuids = set(owners)
    for uuid in sorted(actual_uuids - manifest_uuids):
        fail(errors, f"unmanifested board microvia {uuid}")
    for uuid in sorted(manifest_uuids - actual_uuids):
        fail(errors, f"manifest references missing microvia {uuid}")

    expected_links = set()
    boundary_pairs = {("In2.Cu", "In3.Cu"): "top", ("In4.Cu", "In5.Cu"): "bottom"}
    for core in central:
        for micro in microvias:
            interface = boundary_pairs.get(micro["layers"])
            if core["net"] != micro["net"] or interface is None:
                continue
            if math.dist(core["at"], micro["at"]) <= (core["size"] + micro["size"]) / 2.0 + TOL:
                expected_links.add((core["uuid"], micro["uuid"], core["net"], interface))
    manifest_links = set()
    for link in manifest.get("central_capture_links", []):
        row = (link.get("central_via_uuid"), link.get("microvia_uuid"), link.get("net"), link.get("interface"))
        if row in manifest_links:
            fail(errors, f"duplicate central capture link {row}")
        manifest_links.add(row)
    if manifest_links != expected_links:
        for row in sorted(expected_links - manifest_links):
            fail(errors, f"missing central capture link {row}")
        for row in sorted(manifest_links - expected_links):
            fail(errors, f"unexpected central capture link {row}")
    central_link_pairs = {tuple(sorted((row[0], row[1]))) for row in expected_links}

    layer_index = {name: index for index, name in enumerate(("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"))}
    build_vias = microvias + central
    for i, left in enumerate(build_vias):
        for right in build_vias[i + 1 :]:
            left_depth = min(layer_index[name] for name in left["layers"])
            right_depth = min(layer_index[name] for name in right["layers"])
            if abs(left_depth - right_depth) > 1:
                continue
            distance = math.dist(left["at"], right["at"])
            if distance + TOL >= (left["drill"] + right["drill"]) / 2.0:
                continue
            left_owner = owners.get(left["uuid"])
            right_owner = owners.get(right["uuid"])
            micro_chain = left_owner and right_owner and left_owner[0] == right_owner[0]
            central_chain = tuple(sorted((left["uuid"], right["uuid"]))) in central_link_pairs
            if not micro_chain and not central_chain:
                fail(errors, f"overlapping holes {left['uuid']} and {right['uuid']} are not in one approved stack")
            # In a three-tier coaxial column, the first and third drill barrels
            # also overlap geometrically.  They are allowed because all three
            # vias belong to the same explicitly enumerated chain; only DRC
            # marker approvals below are restricted to adjacent tier pairs.

    exclusions = project.get("board", {}).get("design_settings", {}).get("drc_exclusions", [])
    project_exclusion_signatures = [project_exclusion_signature(item) for item in exclusions]
    expected_exclusions = manifest.get("expected_project_exclusion_signatures", [])
    expected_exclusion_signatures = [project_exclusion_signature(item) for item in expected_exclusions]
    if project_exclusion_signatures != expected_exclusion_signatures:
        fail(errors, "project DRC exclusion signatures differ from exact manifest list")

    marker_counter: Counter = Counter()
    for marker in manifest.get("allowed_drc_markers", []):
        signature = manifest_signature(marker)
        count = int(marker.get("expected_report_count", 0))
        if count <= 0:
            fail(errors, f"allowed marker {signature} has invalid expected_report_count {count}")
        marker_counter[signature] += count
        via_uuids = signature[3]
        if len(via_uuids) != 2:
            fail(errors, f"allowed marker {signature} must name exactly two vias")
            continue
        owner_a = owners.get(via_uuids[0])
        owner_b = owners.get(via_uuids[1])
        micro_pair = owner_a and owner_b and owner_a[0] == owner_b[0] and abs(owner_a[1] - owner_b[1]) == 1
        central_pair = tuple(sorted(via_uuids)) in central_link_pairs
        if not micro_pair and not central_pair:
            fail(errors, f"allowed marker {signature} is not one stack's adjacent-tier pair")

    if drc_path:
        report = json.loads(drc_path.read_text())
        active = [violation for violation in report.get("violations", []) if not violation.get("excluded", False)]
        for violation in active:
            if violation.get("type") not in ACTIVE_MARKER_TYPES:
                fail(errors, f"unexpected active DRC marker {drc_signature(violation)}")
        actual_counter: Counter = Counter()
        board_counter: Counter = Counter()
        for violation in active:
            if violation.get("type") not in ACTIVE_MARKER_TYPES:
                continue
            signature = drc_signature(violation)
            uuids = signature[3]
            owner_a = owners.get(uuids[0]) if len(uuids) == 2 else None
            owner_b = owners.get(uuids[1]) if len(uuids) == 2 else None
            micro_pair = owner_a and owner_b and owner_a[0] == owner_b[0] and abs(owner_a[1] - owner_b[1]) == 1
            central_pair = len(uuids) == 2 and tuple(sorted(uuids)) in central_link_pairs
            if micro_pair or central_pair:
                actual_counter[signature] += 1
            else:
                board_counter[(violation.get("type", ""), marker_rule(violation.get("description", "")))] += 1
        if actual_counter != marker_counter:
            for signature, count in sorted((actual_counter - marker_counter).items(), key=str):
                fail(errors, f"unexpected active stack marker x{count}: {signature}")
            for signature, count in sorted((marker_counter - actual_counter).items(), key=str):
                fail(errors, f"missing expected active stack marker x{count}: {signature}")
        rows = {}
        for row in manifest.get("reviewed_board_markers", []):
            key = (row.get("type"), row.get("rule"))
            if key in rows:
                fail(errors, f"duplicate reviewed board marker row {key}")
            rows[key] = row
        for key in sorted(set(board_counter) - set(rows)):
            fail(errors, f"unreviewed active board marker rule {key} x{board_counter[key]}")
        for key in sorted(set(rows) - set(board_counter)):
            fail(errors, f"reviewed board marker rule {key} reports nothing; manifest is stale")
        report_exclusions = Counter(
            drc_signature(violation)
            for violation in report.get("violations", [])
            if violation.get("excluded", False)
        )
        expected_report_exclusions = Counter(expected_exclusion_signatures)
        if report_exclusions != expected_report_exclusions:
            fail(
                errors,
                "DRC report excluded-marker signatures differ from exact project/manifest exclusions: "
                f"actual={dict(report_exclusions)!r}, expected={dict(expected_report_exclusions)!r}",
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("board_dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--drc-json", type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest or args.board_dir / "stack-manifest.json"
    try:
        errors = validate(args.board_dir, manifest_path, args.drc_json)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"FAIL: validator exception: {exc}")
        return 1
    if errors:
        print(f"FAIL: {len(errors)} stack-manifest issue(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    manifest = json.loads(manifest_path.read_text())
    tier_count = sum(len(stack["tiers"]) for stack in manifest["stacks"])
    marker_count = sum(int(marker["expected_report_count"]) for marker in manifest.get("allowed_drc_markers", []))
    board_rows = manifest.get("reviewed_board_markers", [])
    print(
        "PASS: fail-closed HDI manifest validates "
        f"{len(manifest['stacks'])} stacks / {tier_count} microvias / "
        f"{len(manifest.get('allowed_drc_markers', []))} signatures / {marker_count} DRC reports / "
        f"{len(board_rows)} reviewed board marker rules"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
