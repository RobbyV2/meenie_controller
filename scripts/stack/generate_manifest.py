#!/usr/bin/env python3
"""Generate a schema-4 exact HDI manifest from a frozen Meenie candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

from validate_stacks import (
    ACTIVE_MARKER_TYPES,
    drc_signature,
    marker_rule,
    parse_layer_stack,
    parse_raw_vias,
    via_physical_clearance,
    via_spacing_metrics,
)

CANONICAL = {
    "top": [("F.Cu", "In1.Cu"), ("In1.Cu", "In2.Cu"), ("In2.Cu", "In3.Cu")],
    "bottom": [("In4.Cu", "In5.Cu"), ("In5.Cu", "In6.Cu"), ("In6.Cu", "B.Cu")],
}
TOL = 1e-6


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "unnamed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("board_dir", type=Path)
    parser.add_argument("--drc-json", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    board_path = args.board_dir / "meenie.kicad_pcb"
    project_path = args.board_dir / "meenie.kicad_pro"
    rules_path = args.board_dir / "meenie.kicad_dru"
    output = args.output or args.board_dir / "stack-manifest.json"
    for path in (board_path, project_path, rules_path, args.drc_json):
        if not path.is_file():
            print(f"FAIL: missing {path}")
            return 1

    all_vias = parse_raw_vias(board_path.read_text())
    vias = [via for via in all_vias if via["kind"] == "micro"]
    central = [via for via in all_vias if via["kind"] in ("blind", "buried")]
    invalid_central = [via for via in central if via["layers"] != ("In3.Cu", "In4.Cu")]
    if invalid_central:
        print("FAIL: unsupported blind/buried layer pair(s): " + repr([(v["uuid"], v["layers"]) for v in invalid_central]))
        return 1
    by_uuid = {via["uuid"]: via for via in vias}
    tier_info: dict[str, tuple[str, int]] = {}
    errors: list[str] = []
    for via in vias:
        matches = [
            (face, pairs.index(via["layers"]))
            for face, pairs in CANONICAL.items()
            if via["layers"] in pairs
        ]
        if len(matches) != 1:
            errors.append(f"{via['uuid']} has non-adjacent or ambiguous layers {via['layers']}")
        else:
            tier_info[via["uuid"]] = matches[0]
    if errors:
        print("FAIL: " + "\nFAIL: ".join(errors))
        return 1

    # Capture-pad overlap on consecutive build-up tiers defines a physical chain.
    graph: dict[str, set[str]] = {via["uuid"]: set() for via in vias}
    for index, left in enumerate(vias):
        left_face, left_tier = tier_info[left["uuid"]]
        for right in vias[index + 1 :]:
            right_face, right_tier = tier_info[right["uuid"]]
            if left["net"] != right["net"] or left_face != right_face or abs(left_tier - right_tier) != 1:
                continue
            capture = (left["size"] + right["size"]) / 2.0
            if math.dist(left["at"], right["at"]) <= capture + TOL:
                graph[left["uuid"]].add(right["uuid"])
                graph[right["uuid"]].add(left["uuid"])

    components: list[list[str]] = []
    unseen = set(graph)
    while unseen:
        root = min(unseen)
        queue = deque([root])
        component: set[str] = set()
        while queue:
            uuid = queue.popleft()
            if uuid in component:
                continue
            component.add(uuid)
            queue.extend(graph[uuid] - component)
        unseen -= component
        components.append(sorted(component))

    stacks: list[dict] = []
    owner: dict[str, tuple[str, int]] = {}
    name_counts: Counter = Counter()
    for component in components:
        ordered = sorted(component, key=lambda uuid: tier_info[uuid][1])
        face = tier_info[ordered[0]][0]
        indices = [tier_info[uuid][1] for uuid in ordered]
        nets = {by_uuid[uuid]["net"] for uuid in ordered}
        if len(component) not in (1, 2, 3):
            errors.append(f"component {component} has {len(component)} tiers")
            continue
        if len(nets) != 1 or indices != list(range(indices[0], indices[0] + len(indices))):
            errors.append(f"component {component} is not one contiguous same-net chain: nets={nets}, tiers={indices}")
            continue
        if any(len(graph[uuid]) > 2 for uuid in component):
            errors.append(f"component {component} branches")
            continue
        net = next(iter(nets))
        anchor = by_uuid[ordered[0]]["at"]
        base = f"{face}-{slug(net)}-{anchor[0]:.4f}-{anchor[1]:.4f}"
        name_counts[base] += 1
        name = base if name_counts[base] == 1 else f"{base}-{name_counts[base]}"
        tiers: list[dict] = []
        for tier_index, uuid in enumerate(ordered):
            via = by_uuid[uuid]
            tiers.append(
                {
                    "layers": list(via["layers"]),
                    "center_mm": [via["at"][0], via["at"][1]],
                    "pad_mm": via["size"],
                    "hole_mm": via["drill"],
                    "via_uuid": uuid,
                }
            )
            owner[uuid] = (name, tier_index)
        stacks.append({"name": name, "net": net, "face": face, "tiers": tiers})

    if errors:
        print("FAIL: " + "\nFAIL: ".join(errors))
        return 1

    central_links = []
    link_pairs = set()
    boundary_pairs = {("In2.Cu", "In3.Cu"), ("In4.Cu", "In5.Cu")}
    for core in central:
        for micro in vias:
            if core["net"] != micro["net"] or micro["layers"] not in boundary_pairs:
                continue
            if math.dist(core["at"], micro["at"]) > (core["size"] + micro["size"]) / 2.0 + TOL:
                continue
            pair = tuple(sorted((core["uuid"], micro["uuid"])))
            link_pairs.add(pair)
            central_links.append(
                {
                    "central_via_uuid": core["uuid"],
                    "microvia_uuid": micro["uuid"],
                    "net": core["net"],
                    "interface": "top" if micro["layers"] == ("In2.Cu", "In3.Cu") else "bottom",
                }
            )

    report = json.loads(args.drc_json.read_text())
    active = [violation for violation in report.get("violations", []) if not violation.get("excluded", False)]
    unsupported = [violation for violation in active if violation.get("type") not in ACTIVE_MARKER_TYPES]
    if unsupported:
        print(f"FAIL: source DRC has {len(unsupported)} unsupported active markers")
        return 1

    def stack_pair(uuids: tuple[str, ...]) -> tuple[tuple | None, bool]:
        if len(uuids) != 2:
            return None, False
        a, b = owner.get(uuids[0]), owner.get(uuids[1])
        micro = bool(a and b and a[0] == b[0] and abs(a[1] - b[1]) == 1)
        return (a if micro else None), (micro or tuple(sorted(uuids)) in link_pairs)

    stack_counts: Counter = Counter()
    board_counts: Counter = Counter()
    stack_owner: dict = {}
    for violation in active:
        signature = drc_signature(violation)
        owning, bound = stack_pair(signature[3])
        if bound:
            stack_counts[signature] += 1
            stack_owner[signature] = owning
        else:
            board_counts[(violation.get("type", ""), marker_rule(violation.get("description", "")))] += 1

    markers: list[dict] = []
    for signature, count in sorted(stack_counts.items(), key=str):
        marker_type, x, y, uuids = signature
        a = stack_owner[signature]
        b = owner.get(uuids[1]) if a else None
        markers.append(
            {
                "type": marker_type,
                "position_mm": [x, y],
                "via_uuids": list(uuids),
                "stack": a[0] if a else None,
                "tier_indices": sorted([a[1], b[1]]) if a and b else None,
                "expected_report_count": count,
            }
        )
    board_rows = [
        {
            "type": marker_type,
            "rule": rule,
            "observed_count": count,
        }
        for (marker_type, rule), count in sorted(board_counts.items())
    ]

    project = json.loads(project_path.read_text())
    exclusions = project.get("board", {}).get("design_settings", {}).get("drc_exclusions", [])
    manifest = {
        "schema": 4,
        "board": board_path.name,
        "project": project_path.name,
        "rules": rules_path.name,
        "source_hashes": {
            "board_sha256": sha256(board_path),
            "project_sha256": sha256(project_path),
            "rules_sha256": sha256(rules_path),
        },
        "layer_stack": parse_layer_stack(board_path.read_text()),
        "via_physical_clearance_rule_mm": via_physical_clearance(rules_path.read_text()),
        "via_spacing_observed_mm": via_spacing_metrics(all_vias),
        "allowed_layer_sequences": {face: [list(pair) for pair in pairs] for face, pairs in CANONICAL.items()},
        "allowed_tier_counts": [1, 2, 3],
        "stacks": sorted(stacks, key=lambda stack: stack["name"]),
        "central_buried_transitions": [
            {
                "net": via["net"],
                "layers": list(via["layers"]),
                "center_mm": list(via["at"]),
                "pad_mm": via["size"],
                "hole_mm": via["drill"],
                "via_uuid": via["uuid"],
            }
            for via in sorted(central, key=lambda via: via["uuid"])
        ],
        "central_capture_links": sorted(central_links, key=lambda item: (item["central_via_uuid"], item["microvia_uuid"])),
        "expected_project_exclusion_signatures": [exclusion[0] for exclusion in exclusions],
        "allowed_drc_markers": markers,
        "reviewed_board_markers": board_rows,
    }
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"WROTE {output}: {len(stacks)} stacks / {len(vias)} microvias / "
        f"{len(markers)} marker signatures / {sum(stack_counts.values())} stack reports / "
        f"{sum(board_counts.values())} board-geometry reports over {len(board_rows)} rules"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
