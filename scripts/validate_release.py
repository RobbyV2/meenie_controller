#!/usr/bin/env python3
import argparse, json, math, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "panel"))
sys.path.insert(0, str(HERE / "stack"))
import drlcmp, panel
import validate_stacks as validate_stacks_module
from product import COPPER, drill_specs, file_function, load_manifest, sha256
from validate_stacks import validate as validate_stacks


def job_copper(path):
    job = json.loads(path.read_text())
    return job, {
        item["Path"]: item["FileFunction"]
        for item in job.get("FilesAttributes", [])
        if item.get("FileFunction", "").startswith("Copper,")
    }


def drill_files(folder, prefix):
    start = prefix + "-"
    return {
        path.name[len(start):-4]: path
        for path in folder.glob(start + "*.drl")
    }


def nested_erc(report):
    violations = list(report.get("violations", []))
    for sheet in report.get("sheets", []):
        violations.extend(sheet.get("violations", []))
    return violations


def approval_contract(board_sha, manifest_sha, manifest, expected_drills):
    observed_geometries = [list(item) for item in sorted({
        (float(tier["pad_mm"]), float(tier["hole_mm"]))
        for stack in manifest["stacks"] for tier in stack["tiers"]
    })]
    return {
        "approved": True,
        "vendor": "NextPCB",
        "service": "Advanced PCB",
        "board_sha256": board_sha,
        "manifest_sha256": manifest_sha,
        "construction": "8L 3+2+3",
        "finished_thickness_mm": manifest["layer_stack"]["finished_thickness_mm"],
        "layer_stack": manifest.get("layer_stack"),
        "microvia_geometries_mm": observed_geometries,
        "drill_programs": list(expected_drills),
        "central_in3_in4": bool(manifest.get("central_buried_transitions")),
        "central_transition_count": len(manifest.get("central_buried_transitions", [])),
        "central_transition_geometry_mm": [0.3, 0.15],
        "copper_filled_planarized_sequential_tiers": True,
        "target_microvia_geometry_mm": [0.25, 0.1],
        "target_microvia_annular_ring_mm": 0.075,
        "target_microvia_tier_aspect_ratio": 1.0,
        "rf_50_target_ohms": 50,
        "rf_50_tolerance_percent": 10,
        "rf_50_field_solved_width_mm": 0.115,
        "quote_approved": True,
    }


def read_approval(path, checks):
    if path is None:
        return ["signed NextPCB Advanced DFM/quote approval is missing"]
    try:
        approval = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"vendor DFM approval is unreadable: {exc}"]
    return [f"vendor DFM approval field {key} does not match: {approval.get(key)!r} != {value!r}"
            for key, value in checks.items() if approval.get(key) != value]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board-dir", required=True, type=pathlib.Path)
    parser.add_argument("--board-gerbers", required=True, type=pathlib.Path)
    parser.add_argument("--panel-gerbers", required=True, type=pathlib.Path)
    parser.add_argument("--board-prefix", default="meenie")
    parser.add_argument("--panel-prefix", default="meenie-panel")
    parser.add_argument("--drc", required=True, type=pathlib.Path)
    parser.add_argument("--erc", required=True, type=pathlib.Path)
    parser.add_argument("--stack-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--vendor-dfm-approval", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    bg, pg = args.board_gerbers, args.panel_gerbers
    manifest = load_manifest(args.stack_manifest)
    if manifest.get("schema") != 3:
        print(f"FAIL: stack manifest schema must be 3, got {manifest.get('schema')!r}; "
              f"regenerate with scripts/stack/generate_manifest.py")
        return 1
    hard, blockers, dispositions, data = [], [], [], {}
    board_path = args.board_dir / manifest["board"]
    source_outline = panel.outline(str(board_path))[2:]
    dims = {
        "board_mm": [panel.BW, panel.BH],
        "panel_mm": [panel.W, panel.H],
        "grid": [panel.COLS, panel.ROWS],
        "board_count": panel.BOARDS,
    }
    data.update(dims)
    if dims["board_mm"] != list(source_outline):
        hard.append(f"panel board outline {dims['board_mm']} differs from {manifest['board']} {list(source_outline)}")
    if dims["grid"] != [panel.DERIVED_COLS, panel.DERIVED_ROWS]:
        hard.append(f"panel grid {dims['grid']} is not the maximal fit {[panel.DERIVED_COLS, panel.DERIVED_ROWS]}")
    if panel.W > 100 or panel.H > 100 or panel.RAIL < 5:
        hard.append("panel violates 100 x 100 mm / 5 mm rail contract")

    expected_copper = {f"{args.board_prefix}-{suffix}": function for suffix, function in COPPER}
    expected_panel_copper = {f"{args.panel_prefix}-{suffix}": function for suffix, function in COPPER}
    try:
        board_job, board_copper = job_copper(bg / f"{args.board_prefix}-job.gbrjob")
        panel_job, panel_copper = job_copper(pg / f"{args.panel_prefix}-job.gbrjob")
    except (OSError, json.JSONDecodeError) as exc:
        hard.append(f"Gerber job file unreadable: {exc}")
        board_job, board_copper, panel_copper = {}, {}, {}
    if board_copper != expected_copper:
        hard.append(f"unit Gerber X2 copper set differs: {board_copper!r}")
    if panel_copper != expected_panel_copper:
        hard.append(f"panel Gerber X2 copper set differs: {panel_copper!r}")
    for folder, prefix, expected in ((bg, args.board_prefix, expected_copper), (pg, args.panel_prefix, expected_panel_copper)):
        for name, function in expected.items():
            path = folder / name
            if not path.is_file() or file_function(path) != function:
                hard.append(f"{path} lacks exact X2 FileFunction {function}")
    specs = dict(drill_specs(manifest))
    expected_drills = set(specs)
    unit_drills = drill_files(bg, args.board_prefix)
    panel_drills = drill_files(pg, args.panel_prefix)
    if set(unit_drills) != expected_drills:
        hard.append(f"unit drill set {sorted(unit_drills)} != {sorted(expected_drills)}")
    if set(panel_drills) != expected_drills:
        hard.append(f"panel drill set {sorted(panel_drills)} != {sorted(expected_drills)}")
    inventories = {}
    for suffix in sorted(expected_drills & set(unit_drills) & set(panel_drills)):
        unit_tools, unit_hits = drlcmp.parse(str(unit_drills[suffix]))
        panel_tools, panel_hits = drlcmp.parse(str(panel_drills[suffix]))
        unit_diameters = sorted(unit_tools.values())
        panel_diameters = sorted(panel_tools.values())
        inventories[suffix] = {
            "file_function": file_function(unit_drills[suffix]),
            "unit_tools_mm": unit_diameters,
            "panel_tools_mm": panel_diameters,
            "unit_hits": len(unit_hits),
            "panel_hits": len(panel_hits),
        }
        if file_function(unit_drills[suffix]) != specs[suffix] or file_function(panel_drills[suffix]) != specs[suffix]:
            hard.append(f"{suffix} drill X2 FileFunction differs from {specs[suffix]}")
        if suffix == "NPTH":
            if len(panel_hits) != len(unit_hits) * panel.BOARDS + panel.PANEL_NPTH:
                hard.append(f"panel NPTH hits {len(panel_hits)} != {panel.BOARDS} x {len(unit_hits)} + {panel.PANEL_NPTH}")
            required_panel_tools = sorted(set(unit_diameters) | {panel.BITE_D, 3.05})
            if panel_diameters != required_panel_tools:
                hard.append(f"panel NPTH tools {panel_diameters} != {required_panel_tools}")
        else:
            if len(panel_hits) != len(unit_hits) * panel.BOARDS:
                hard.append(f"{suffix} panel hits {len(panel_hits)} != {panel.BOARDS} x {len(unit_hits)}")
            if panel_diameters != unit_diameters:
                hard.append(f"{suffix} panel tools {panel_diameters} != unit {unit_diameters}")
    data["drill_inventory"] = inventories
    for name in ("netlist.ipc", "ipc2581.xml"):
        if not (bg.parent / name).is_file() or not (pg.parent / name).is_file():
            hard.append(f"missing unit or panel {name}")

    stack_errors = validate_stacks(args.board_dir, args.stack_manifest, args.drc)
    data["stack_gate"] = {"passed": not stack_errors, "issues": stack_errors}
    hard.extend(f"stack gate: {error}" for error in stack_errors)
    drc = json.loads(args.drc.read_text())
    active = [item for item in drc.get("violations", []) if not item.get("excluded")]
    excluded = [item for item in drc.get("violations", []) if item.get("excluded")]
    unconnected_items = drc.get("unconnected_items", [])
    unconnected = len(unconnected_items)
    parity = len(drc.get("schematic_parity", []))
    data["drc"] = {
        "active_manifested_hole_reports": len(active),
        "excluded": len(excluded),
        "unconnected": unconnected,
        "unconnected_endpoints": [
            [item.get("description") for item in edge.get("items", [])]
            for edge in unconnected_items
        ],
        "parity": parity,
    }
    if parity:
        hard.append(f"schematic parity has {parity} item(s)")
    if unconnected:
        blockers.append(f"board has {unconnected} unconnected item(s)")
    expected_npth_uuids = {
        signature[3]
        for signature in map(validate_stacks_module.project_exclusion_signature, manifest["expected_project_exclusions"])
        if signature[0] == "npth_inside_courtyard"
    }
    exact_npth = (len(excluded) == 1 and excluded[0].get("type") == "npth_inside_courtyard" and
                  tuple(sorted(item.get("uuid") for item in excluded[0].get("items", []))) in expected_npth_uuids)
    if excluded and not exact_npth:
        hard.append("native DRC exclusion is not the one exact J1/U1 NPTH disposition")
    if exact_npth:
        # validate_stacks has already required the exact project exclusion list
        # and the exact excluded DRC marker signature.  This is an intentional
        # J1 locating-peg/U1 courtyard geometry, not an unreviewed suppression.
        dispositions.append({
            "type": "native_drc_exclusion",
            "status": "accepted_manifest_bound",
            "count": len(excluded),
            "reason": "exact J1 NPTH locating peg inside U1 courtyard; keyed by marker and both UUIDs",
        })
    erc = json.loads(args.erc.read_text())
    erc_live = [item for item in nested_erc(erc) if not item.get("excluded")]
    data["erc"] = {"live": len(erc_live)}
    if erc_live:
        hard.append(f"ERC has {len(erc_live)} active item(s)")

    thickness = board_job.get("GeneralSpecs", {}).get("BoardThickness")
    data["finished_thickness_mm"] = thickness
    released = manifest["layer_stack"]["finished_thickness_mm"]
    if not isinstance(thickness, (int, float)) or not math.isclose(float(thickness), released, abs_tol=1e-6):
        hard.append(f"Gerber job thickness {thickness!r} is not exact released {released} mm")
    board_sha = sha256(board_path)
    manifest_sha = sha256(args.stack_manifest)
    observed_geometries = [list(item) for item in sorted({
        (float(tier["pad_mm"]), float(tier["hole_mm"]))
        for stack in manifest["stacks"] for tier in stack["tiers"]
    })]
    central = manifest.get("central_buried_transitions", [])
    data["required_dfm_scope"] = {
        "layer_stack": manifest.get("layer_stack"),
        "microvia_geometries_mm": observed_geometries,
        "central_in3_in4": bool(central),
        "central_transitions": central,
        "drill_programs": sorted(expected_drills),
        "copper_filled_planarized_sequential_tiers": True,
        "target_microvia_geometry_mm": [0.25, 0.1],
        "target_microvia_annular_ring_mm": 0.075,
        "target_microvia_tier_aspect_ratio": 1.0,
        "central_transition_geometry_mm": [0.3, 0.15],
        "rf_50_target_ohms": 50,
        "rf_50_tolerance_percent": 10,
        "rf_50_field_solved_width_mm": 0.115,
    }
    required_approval = approval_contract(board_sha, manifest_sha, manifest, sorted(expected_drills))
    data["required_vendor_approval"] = required_approval
    dfm = read_approval(args.vendor_dfm_approval, required_approval)
    blockers.extend(dfm)
    dispositions.append({
        "type": "manifested_stack_drc_reports",
        "status": "accepted_manifest_bound_pending_vendor_dfm",
        "count": len(active),
        "microvia_geometries_mm": observed_geometries,
        "central_transition_count": len(central),
        "reason": "only exact adjacent sequential-tier capture reports enumerated by schema-3 manifest",
    })
    data["source"] = {"board_sha256": board_sha, "manifest_sha256": manifest_sha}
    data["vendor_dfm"] = {"passed": not dfm, "issues": dfm}
    data["dry_run"] = args.dry_run
    data["accepted_dispositions"] = dispositions
    data["hard_failures"] = hard
    data["release_blockers"] = blockers
    data["orderable"] = not args.dry_run and not hard and not blockers
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 1 if hard or (blockers and not args.dry_run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
