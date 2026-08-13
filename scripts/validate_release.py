#!/usr/bin/env python3
import argparse, datetime, json, math, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "panel"))
sys.path.insert(0, str(HERE / "stack"))
import drlcmp, panel, sexp
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


def audit_summary(path, board_sha, name, required=None):
    report = json.loads(path.read_text())
    issues = list(report.get("issues", []))
    if not report.get("passed") and not issues:
        issues.append(f"{name} did not pass")
    if report.get("board_sha256") != board_sha:
        issues.append(f"{name} is not bound to the released board SHA-256")
    if required is not None:
        required(report, issues)
    return report, issues, {
        "passed": not issues,
        "issues": issues,
        "report": f"reports/{path.name}",
        "sha256": sha256(path),
    }


def rf_50_geometry(board_path):
    """Return the source-of-truth RF_50 copper segments from the board."""
    tree = sexp.parse(board_path.read_text())
    net_names = {}
    for node in tree:
        if sexp.tag(node) == "net" and len(node) >= 3:
            net_names[node[1]] = sexp.unq(node[2])
    segments = []
    for node in tree:
        if sexp.tag(node) != "segment":
            continue
        net = sexp.kid(node, "net")
        if net is None:
            continue
        net_value = sexp.unq(net[1])
        net_name = net_names.get(net_value, net_value)
        if net_name != "RF_50":
            continue
        start, end = sexp.kid(node, "start"), sexp.kid(node, "end")
        width, layer = sexp.kid(node, "width"), sexp.kid(node, "layer")
        if None in (start, end, width, layer):
            raise ValueError("RF_50 segment lacks start, end, width, or layer")
        x1, y1, x2, y2 = map(float, (start[1], start[2], end[1], end[2]))
        segments.append({
            "start_mm": [x1, y1],
            "end_mm": [x2, y2],
            "width_mm": float(width[1]),
            "layer": sexp.unq(layer[1]),
            "length_mm": math.hypot(x2 - x1, y2 - y1),
        })
    return segments


def approval_contract(board_sha, panel_sha, manifest_sha, netlist_sha, assembly_hashes,
                      manifest, expected_drills):
    observed_geometries = [list(item) for item in sorted({
        (float(tier["pad_mm"]), float(tier["hole_mm"]))
        for stack in manifest["stacks"] for tier in stack["tiers"]
    })]
    central = manifest.get("central_buried_transitions", [])
    central_geometries = [list(item) for item in sorted({
        (float(via["pad_mm"]), float(via["hole_mm"])) for via in central
    })]
    central_core_thickness = next(
        float(row["thickness_mm"])
        for row in manifest["layer_stack"]["rows"]
        if row["name"] == "dielectric 4" and row["type"] == "core"
    )
    stack_tier_counts = [len(stack["tiers"]) for stack in manifest["stacks"]]
    return {
        "approved": True,
        "vendor": "NextPCB",
        "service": "Advanced PCB",
        "board_sha256": board_sha,
        "panel_sha256": panel_sha,
        "manifest_sha256": manifest_sha,
        "electrical_test_netlist_sha256": netlist_sha,
        "first_article_assembly_sha256": assembly_hashes,
        "order_customer_panel_quantity": 1,
        "order_assembled_unit_quantity": 35,
        "order_delivery_format": "depanelled_individual_units",
        "vendor_depanel_responsibility": True,
        "small_unit_inside_customer_panel_approved": True,
        "construction": "8L 3+2+3",
        "surface_finish": "ENIG",
        "solder_mask_front_color": "green",
        "solder_mask_back_color": "green",
        "solder_mask_nominal_thickness_mm": 0.01,
        "laminate_minimum_tg_c": 170,
        "laminate_minimum_td_c": 340,
        "laminate_caf_resistant": True,
        "vendor_material_reference_required": True,
        "finished_thickness_mm": manifest["layer_stack"]["finished_thickness_mm"],
        "minimum_accepted_finished_thickness_mm": 0.9,
        "j1_minimum_post_containment_at_accepted_thickness_mm": 0.15,
        "layer_stack": manifest.get("layer_stack"),
        "microvia_geometries_mm": observed_geometries,
        "drill_programs": list(expected_drills),
        "central_in3_in4": bool(central),
        "central_transition_count": len(central),
        "central_transition_geometries_mm": central_geometries,
        "central_transition_process": "mechanically_drilled_buried_core_via",
        "central_transition_core_thickness_mm": central_core_thickness,
        "central_transition_maximum_aspect_ratio": round(
            central_core_thickness / min(float(via["hole_mm"]) for via in central), 6
        ) if central else 0,
        "copper_filled_planarized_sequential_tiers": True,
        "stacked_microvia_columns_approved": True,
        "maximum_microvia_tiers": max(stack_tier_counts, default=0),
        "three_tier_stack_count": sum(count == 3 for count in stack_tier_counts),
        "three_tier_stacked_microvia_process_qualified": True,
        "ipc_6012_class_3_fabrication": True,
        "bare_board_electrical_test_percent": 100,
        "bare_board_electrical_test_netlist_format": "IPC-D-356",
        "bare_board_electrical_test_golden_netlist": "fab/netlist.ipc",
        "bare_board_electrical_test_step_repeat_isolated_units": 35,
        "panel_generated_ipc_d_356_prohibited": True,
        "first_article_stack_microsection": True,
        "first_article_stack_microsection_faces": ["top", "bottom"],
        "target_microvia_geometry_mm": [0.25, 0.15],
        "target_microvia_annular_ring_mm": 0.05,
        "target_microvia_tier_aspect_ratio": 0.666667,
        "maximum_actual_microvia_tier_aspect_ratio": 0.666667,
        "maximum_filled_via_dimple_um": 10,
        "microvia_land_breakout_allowed": False,
        "representative_three_tier_top_bottom_microsections_required": True,
        "filled_capped_planarized_drill_programs": [
            {"program": "F.Cu-In1.Cu", "count": 24, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "In1.Cu-In2.Cu", "count": 18, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "In2.Cu-In3.Cu", "count": 20, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "In4.Cu-In5.Cu", "count": 24, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "In5.Cu-In6.Cu", "count": 47, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "In6.Cu-B.Cu", "count": 77, "pad_mm": 0.25, "hole_mm": 0.15},
            {"program": "in3-in4", "count": 17, "pad_mm": 0.33, "hole_mm": 0.15},
            {"program": "PTH", "count": 7, "pad_mm": 0.33, "hole_mm": 0.15},
        ],
        "panel_edge_breakout_pth_count": 15,
        "panel_edge_breakout_pth_fill_cap_requirement": False,
        "minimum_advanced_hdi_copper_clearance_mm": 0.08,
        "minimum_laser_hole_to_foreign_copper_mm": 0.08,
        "minimum_different_net_via_physical_copper_clearance_rule_mm":
            manifest["via_physical_clearance_rule_mm"]["different_nets"],
        "different_net_via_spacing_observed_mm": manifest["via_spacing_observed_mm"],
        "nextpcb_general_different_net_hole_edge_clearance_mm": 0.3,
        "different_net_via_hole_edge_clearance_deviation_approved": True,
        "minimum_mechanical_via_annular_ring_mm": 0.09,
        "mechanical_pth_finished_hole_mm": 0.15,
        "mechanical_pth_finished_hole_count": 7,
        "panel_mouse_bite_hole_mm": 0.5,
        "panel_mouse_bite_pitch_mm": 0.85,
        "panel_mouse_bite_minimum_copper_gap_mm": 0.35,
        "panel_mouse_bite_process_envelope_mm": 0.6,
        "panel_edge_pth_count": 15,
        "panel_edge_pth_copper_to_edge_mm": 0.249999,
        "panel_edge_pth_vendor_minimum_copper_to_edge_mm": 0.2,
        "panel_edge_pth_actual_post_depanel_minimum_copper_to_edge_mm": 0.2,
        "panel_edge_profile_tolerance_maximum_mm": 0.05,
        "panel_global_fiducial_count": 3,
        "panel_tooling_hole_count": 4,
        "panel_supported_low_stress_cut_or_nibble_depaneling_required": True,
        "panel_hand_snapping_prohibited": True,
        "panel_edge_pth_post_depanel_inspection_required": True,
        "panel_discard_damaged_units_required": True,
        "rf_50_target_ohms": 50,
        "rf_50_tolerance_percent": 10,
        "rf_50_field_solved_width_mm": 0.17,
        "rf_50_field_solved_impedance_required_range_ohms": [45, 55],
        "rf_50_field_solve_report_reference_required": True,
        "vendor_pressed_stack_reference_required": True,
        "first_article_impedance_coupon_required": True,
        "first_article_tdr_report_required": True,
        "rf_50_artwork_length_mm": 4.113,
        "rf_50_in6_reference_preserved": True,
        "rf_matching_inner_keepout_deviation_acknowledged": True,
        "rf_first_article_vna_and_ota_required": True,
        "nordic_regulator_decoupling_mpn": "GRM188R61E475KE11D",
        "nordic_regulator_decoupling_lcsc_part": "C90057",
        "nordic_regulator_decoupling_references": ["C6", "C9", "C11"],
        "nordic_regulator_decoupling_no_substitution": True,
        "nordic_decusb_effective_capacitance_minimum_uf": 2.35,
        "nordic_vdd_effective_capacitance_minimum_uf": 2.7,
        "dec5_capacitor_mpn": "GRM0335C1E821JA01D",
        "dec5_capacitor_dielectric": "C0G/NP0",
        "dec5_capacitor_tolerance_percent": 5,
        "hfxo_crystal_mpn": "X1E0002510059",
        "hfxo_crystal_load_capacitance_pf": 8,
        "hfxo_crystal_esr_max_ohm": 100,
        "hfxo_crystal_shunt_capacitance_pf": 0.47,
        "hfxo_crystal_frequency_tolerance_ppm": 10,
        "hfxo_crystal_operating_temperature_c": [-20, 75],
        "hfxo_load_capacitor_mpn": "GRM0335C1H120GA01D",
        "hfxo_load_capacitor_value_pf": 12,
        "hfxo_load_capacitor_tolerance_percent": 2,
        "hfxo_conservative_ble_budget_ppm": 24.397,
        "hfxo_foreign_inner_copper_qualification_required": True,
        "hfxo_first_article_startup_and_frequency_test": True,
        "d2_ground_return_visual_and_xray_inspection": True,
        "d2_vbus_esd_mpn": "ESD441DPLR",
        "d2_vbus_esd_reverse_standoff_v": 5.5,
        "d3_cathode_pad1_ground_and_solder_wetting_inspection": True,
        "d3_led_mpn": "LTST-C281KRKT",
        "d3_land_terminal_gap_mm": 0.35,
        "d3_land_length_mm": 0.65,
        "d3_land_width_mm": 0.65,
        "d3_manufacturer_suggested_land_width_mm": 0.9,
        "d3_minimum_copper_to_board_edge_mm": 0.21,
        "d3_compact_land_deviation_approved": True,
        "j1_usb_connector_mpn": "TYPE-C 16P QTWT",
        "j1_usb_connector_lcsc_part": "C5187472",
        "j1_usb_connector_external_procurement_route": "LCSC C5187472",
        "j1_usb_connector_no_substitution": True,
        "j1_usb_connector_fit_and_shell_solder_inspection": True,
        "j1_locating_post_nominal_mm": 0.60,
        "j1_locating_post_maximum_mm": 0.75,
        "j1_locating_post_minimum_board_containment_mm": 0.241,
        "u1_signal_pad_count": 73,
        "u1_signal_copper_pad_diameter_mm": 0.25,
        "u1_signal_solder_mask_opening_mm": 0.375,
        "u1_signal_paste_mask_opening_mm": 0.3,
        "u1_nordic_recommended_copper_pad_diameter_mm": 0.275,
        "u1_reduced_copper_land_deviation_approved": True,
        "u1_first_article_xray_and_solderability_inspection": True,
        "c19_vddh_input_capacitor_mpn": "CL05A475MO5NUNC",
        "c19_vddh_input_capacitor_lcsc_part": "C318563",
        "c19_vddh_input_capacitor_package": "0402",
        "c19_vddh_input_capacitor_nominal_uf": 4.7,
        "c19_vddh_input_capacitor_tolerance_percent": 20,
        "c19_vddh_input_capacitor_dielectric": "X5R",
        "c19_vddh_input_capacitor_rated_voltage_v": 16,
        "c19_dc_bias_characterization_source": "Samsung CL05A475MO5NUNC official DC Bias Characteristics",
        "c19_effective_capacitance_typical_at_5p5v_uf": 1.642,
        "c19_effective_capacitance_at_5p5v_after_negative_initial_tolerance_uf": 1.313,
        "c19_characterization_excludes_temperature_and_aging": True,
        "c19_total_nominal_connector_vbus_capacitance_uf": 9.4,
        "usb_c_sink_vbus_capacitance_maximum_uf": 10.0,
        "c19_nordic_reference_package": "0603",
        "c19_nordic_reference_dielectric": "X7S",
        "c19_nordic_reference_tolerance_percent": 10,
        "c19_package_and_dielectric_deviation_acknowledged": True,
        "c19_no_substitution": True,
        "c19_first_article_vbus_hotplug_and_load_transient_test": True,
        "usb_full_speed_first_article_function_test": True,
        "usb_connector_system_esd_qualification": True,
        "double_sided_turnkey_assembly": True,
        "minimum_assembly_package": "0201 imperial / 0603 metric",
        "ipc_a_610_class_3_assembly": True,
        "j_std_001_class_3_assembly": True,
        "assembly_spi_percent": 100,
        "assembly_aoi_percent": 100,
        "u1_assembly_axi_percent": 100,
        "assembly_inspection_report_required": True,
        "first_article_rf_population": "B-4n7-0p8",
        "first_article_delivered_unprogrammed": True,
        "u1_first_article_mpn": "NRF52840-QIAA-R",
        "u1_first_article_nrnd_status_acknowledged": True,
        "u1_active_successor_order_code": "NRF52840-QIAA-F-R",
        "u1_active_successor_requires_firmware_security_requalification": True,
        "assembly_solder_alloy": "SAC305_lead_free",
        "vendor_stencil_and_paste_process_approval_required": True,
        "vendor_stencil_plan_must_meet_sw1_and_u1_j1_volume_requirements": True,
        "parts_substitution_policy": "none_without_written_customer_approval_and_release_rerun",
        "sw1_switch_mpn": "GSNANOT000102",
        "sw1_land_size_mm": [0.5, 1.4],
        "sw1_land_pitch_mm": 2.3,
        "sw1_paste_thickness_mm": 0.08,
        "sw1_paste_thickness_tolerance_mm": 0.01,
        "sw1_maximum_reflow_passes": 2,
        "undocumented_cam_geometry_edits_prohibited": True,
        "cam_change_delta_checkplot_and_customer_reapproval_required": True,
        "assembly_procurement_routes_approved": True,
        "thin_stock_procurement": [
            {"reference": "D1", "quantity": 39, "manufacturer": "Texas Instruments",
             "mpn": "TPD1E01B04DPLR", "route": "DigiKey 296-47363-1-ND"},
            {"reference": "R1", "quantity": 39, "manufacturer": "Yageo",
             "mpn": "RC0402FR-072R2L", "route": "DigiKey 311-2.20LRCT-ND"},
            {"reference": "R2,R3", "quantity": 77, "manufacturer": "Yageo",
             "mpn": "RC0201FR-075K1L", "route": "LCSC C274341"},
            {"reference": "SW1", "quantity": 39, "manufacturer": "C&K/Littelfuse",
             "mpn": "GSNANOT000102", "route": "DigiKey 108-NANOT160ASCT-ND"},
        ],
        "quote_approved": True,
    }


SIGNATURE_FIELDS = ("vendor_signer_name", "vendor_signer_email", "vendor_quote_number", "vendor_signed_at")
VENDOR_EVIDENCE_FIELDS = (
    "rf_50_field_solve_report_reference",
    "vendor_pressed_stack_reference",
    "vendor_material_reference",
    "vendor_three_tier_stack_process_qualification_reference",
    "vendor_microsection_reference",
    "vendor_stencil_process_reference",
    "vendor_cam_checkplot_reference",
    "vendor_edge_clearance_inspection_reference",
    "vendor_ipc_class_3_certificate_reference",
)


def read_approval(path, checks):
    if path is None:
        return ["missing"]
    try:
        approval = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ["unreadable"]
    issues = [f"mismatch:{key}"
              for key, value in checks.items() if approval.get(key) != value]
    issues.extend(f"missing:{key}"
                  for key in SIGNATURE_FIELDS
                  if not isinstance(approval.get(key), str) or not approval[key].strip())
    issues.extend(f"missing:{key}"
                  for key in VENDOR_EVIDENCE_FIELDS
                  if not isinstance(approval.get(key), str) or not approval[key].strip())
    evidence = approval.get("vendor_evidence_sha256")
    if not isinstance(evidence, dict):
        issues.append("invalid:vendor_evidence_sha256")
        evidence = {}
    approval_dir = path.parent.resolve()
    for key in VENDOR_EVIDENCE_FIELDS:
        reference = approval.get(key)
        if not isinstance(reference, str) or not reference.strip():
            continue
        pure = pathlib.PurePath(reference)
        if pure.is_absolute() or ".." in pure.parts or pure.name != reference:
            issues.append(f"invalid:{key}")
            continue
        evidence_path = approval_dir / reference
        digest = evidence.get(reference)
        if not isinstance(digest, str) or len(digest) != 64:
            issues.append(f"missing:vendor_evidence_sha256:{reference}")
        elif not evidence_path.is_file():
            issues.append(f"missing:vendor_evidence_file:{reference}")
        elif sha256(evidence_path) != digest.lower():
            issues.append(f"mismatch:vendor_evidence_sha256:{reference}")
    signer_email = approval.get("vendor_signer_email")
    if isinstance(signer_email, str) and signer_email.strip():
        address = signer_email.strip()
        local, separator, domain = address.partition("@")
        if (separator != "@" or not local or not domain or "@" in domain or
                any(character.isspace() for character in address) or "." not in domain):
            issues.append("invalid:vendor_signer_email")
    signed_at = approval.get("vendor_signed_at")
    if isinstance(signed_at, str) and signed_at.strip():
        try:
            parsed_signed_at = datetime.datetime.fromisoformat(
                signed_at.strip().replace("Z", "+00:00")
            )
        except ValueError:
            parsed_signed_at = None
        if parsed_signed_at is None or parsed_signed_at.utcoffset() is None:
            issues.append("invalid:vendor_signed_at")
    solved = approval.get("rf_50_field_solved_impedance_ohms")
    if not isinstance(solved, (int, float)) or isinstance(solved, bool) or not math.isfinite(solved):
        issues.append("invalid:rf_50_field_solved_impedance_ohms")
    elif not 45.0 <= float(solved) <= 55.0:
        issues.append("out_of_range:rf_50_field_solved_impedance_ohms")
    return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board-dir", required=True, type=pathlib.Path)
    parser.add_argument("--board-gerbers", required=True, type=pathlib.Path)
    parser.add_argument("--panel-gerbers", required=True, type=pathlib.Path)
    parser.add_argument("--panel-board", required=True, type=pathlib.Path)
    parser.add_argument("--board-prefix", default="meenie")
    parser.add_argument("--panel-prefix", default="meenie-panel")
    parser.add_argument("--drc", required=True, type=pathlib.Path)
    parser.add_argument("--erc", required=True, type=pathlib.Path)
    parser.add_argument("--stack-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--circuit-audit", required=True, type=pathlib.Path)
    parser.add_argument("--rf-audit", required=True, type=pathlib.Path)
    parser.add_argument("--usb-audit", required=True, type=pathlib.Path)
    parser.add_argument("--power-audit", required=True, type=pathlib.Path)
    parser.add_argument("--hfxo-audit", required=True, type=pathlib.Path)
    parser.add_argument("--mechanical-fit-audit", required=True, type=pathlib.Path)
    parser.add_argument("--panel-assembly-audit", required=True, type=pathlib.Path)
    parser.add_argument("--assembly-dir", required=True, type=pathlib.Path)
    parser.add_argument("--stock-audit", required=True, type=pathlib.Path)
    parser.add_argument("--vendor-dfm-approval", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    bg, pg = args.board_gerbers, args.panel_gerbers
    manifest = load_manifest(args.stack_manifest)
    if manifest.get("schema") != 4:
        print(f"FAIL: stack manifest schema must be 4, got {manifest.get('schema')!r}; "
              f"regenerate with scripts/stack/generate_manifest.py")
        return 1
    hard, blockers, data = [], [], {}
    board_path = args.board_dir / manifest["board"]
    panel_path = args.panel_board
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

    try:
        rf_segments = rf_50_geometry(board_path)
        rf_widths = sorted({segment["width_mm"] for segment in rf_segments})
        rf_layers = sorted({segment["layer"] for segment in rf_segments})
        rf_length = sum(segment["length_mm"] for segment in rf_segments)
        data["rf_50_geometry"] = {
            "segment_count": len(rf_segments),
            "widths_mm": rf_widths,
            "layers": rf_layers,
            "artwork_length_mm": round(rf_length, 6),
            "segments": rf_segments,
        }
        if len(rf_segments) != 5:
            hard.append(f"RF_50 has {len(rf_segments)} segments, not exact released 5")
        if rf_widths != [0.17]:
            hard.append(f"RF_50 widths {rf_widths} differ from exact released [0.17] mm")
        if rf_layers != ["B.Cu"]:
            hard.append(f"RF_50 layers {rf_layers} differ from exact released ['B.Cu']")
        if not math.isclose(rf_length, 4.113, abs_tol=1e-6):
            hard.append(f"RF_50 artwork length {rf_length:.6f} mm differs from exact released 4.113 mm")
    except (OSError, ValueError, IndexError) as exc:
        data["rf_50_geometry"] = {"issues": [str(exc)]}
        hard.append(f"RF_50 source geometry is unreadable: {exc}")

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
            "unit_hit_counts_by_tool_mm": {
                str(diameter): sum(hit[0] == diameter for hit in unit_hits)
                for diameter in unit_diameters
            },
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
    pth_015 = inventories.get("PTH", {}).get("unit_hit_counts_by_tool_mm", {}).get("0.15")
    if pth_015 != 7:
        hard.append(f"PTH program has {pth_015!r} 0.150 mm finished holes, not exact released 7")
    netlist_path = bg.parent.parent / "netlist.ipc"
    if not netlist_path.is_file():
        hard.append("missing unit IPC-D-356 golden netlist")

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
        "active_violations": len(active),
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
    if active:
        hard.append(f"board DRC has {len(active)} active violation(s)")
    if unconnected:
        hard.append(f"board has {unconnected} unconnected item(s)")
    expected_npth_uuids = {
        signature[3]
        for signature in map(validate_stacks_module.project_exclusion_signature,
                             manifest["expected_project_exclusion_signatures"])
        if signature[0] == "npth_inside_courtyard"
    }
    exact_npth = (len(excluded) == 1 and excluded[0].get("type") == "npth_inside_courtyard" and
                  tuple(sorted(item.get("uuid") for item in excluded[0].get("items", []))) in expected_npth_uuids)
    if excluded and not exact_npth:
        hard.append("native DRC exclusion is not the one exact J1/U1 NPTH disposition")
    erc = json.loads(args.erc.read_text())
    erc_live = [item for item in nested_erc(erc) if not item.get("excluded")]
    data["erc"] = {"live": len(erc_live)}
    if erc_live:
        hard.append(f"ERC has {len(erc_live)} active item(s)")

    try:
        circuit = json.loads(args.circuit_audit.read_text())
        circuit_issues = list(circuit.get("issues", []))
        if not circuit.get("passed") and not circuit_issues:
            circuit_issues.append("circuit audit did not pass")
        data["circuit_audit"] = {
            "passed": not circuit_issues,
            "critical_net_count": circuit.get("critical_net_count"),
            "gpio_breakout_count": circuit.get("gpio_breakout_count"),
            "power_mode": circuit.get("power_mode"),
            "issues": circuit_issues,
        }
        hard.extend(f"circuit audit: {issue}" for issue in circuit_issues)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        data["circuit_audit"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"circuit audit is missing or invalid: {exc}")

    try:
        stock = json.loads(args.stock_audit.read_text())
        checked = datetime.datetime.fromisoformat(stock["checked_utc"])
        if checked.tzinfo is None:
            raise ValueError("checked_utc lacks timezone")
        age = datetime.datetime.now(datetime.timezone.utc) - checked
        stock_issues = list(stock.get("issues", []))
        if age > datetime.timedelta(hours=24):
            stock_issues.append(f"stock audit is {age.total_seconds() / 3600:.1f} hours old")
        if not stock.get("passed") and not stock_issues:
            stock_issues.append("stock audit did not pass")
        data["stock_audit"] = {
            "passed": not stock_issues,
            "checked_utc": stock["checked_utc"],
            "part_count": len(stock.get("parts", [])),
            "policy": stock.get("policy"),
            "issues": stock_issues,
        }
        if stock_issues:
            blockers.append("stock_audit")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        data["stock_audit"] = {"passed": False, "issues": [str(exc)]}
        blockers.append("stock_audit")

    thickness = board_job.get("GeneralSpecs", {}).get("BoardThickness")
    data["finished_thickness_mm"] = thickness
    released = manifest["layer_stack"]["finished_thickness_mm"]
    if not isinstance(thickness, (int, float)) or not math.isclose(float(thickness), released, abs_tol=1e-6):
        hard.append(f"Gerber job thickness {thickness!r} is not exact released {released} mm")
    board_sha = sha256(board_path)
    try:
        rf_audit, rf_issues, data["rf_reference_audit"] = audit_summary(
            args.rf_audit, board_sha, "RF reference audit",
            lambda report, issues: issues.append("RF reference audit lacks five exact match components")
            if len(report.get("matching_components", {})) != 5 else None,
        )
        hard.extend(f"RF reference audit: {issue}" for issue in rf_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        data["rf_reference_audit"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"RF reference audit is missing or invalid: {exc}")
    try:
        usb, usb_issues, data["usb_interface"] = audit_summary(
            args.usb_audit, board_sha, "USB interface audit")
        hard.extend(f"USB interface audit: {issue}" for issue in usb_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        data["usb_interface"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"USB interface audit is missing or invalid: {exc}")
    try:
        power, power_issues, data["power_geometry"] = audit_summary(
            args.power_audit, board_sha, "power geometry audit")
        hard.extend(f"power geometry audit: {issue}" for issue in power_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        data["power_geometry"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"power geometry audit is missing or invalid: {exc}")
    try:
        hfxo, hfxo_issues, data["hfxo"] = audit_summary(
            args.hfxo_audit, board_sha, "HFXO audit")
        hard.extend(f"HFXO audit: {issue}" for issue in hfxo_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        data["hfxo"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"HFXO audit is missing or invalid: {exc}")
    try:
        mechanical, mechanical_issues, data["mechanical_fit"] = audit_summary(
            args.mechanical_fit_audit, board_sha, "mechanical fit audit")
        hard.extend(f"mechanical fit audit: {issue}" for issue in mechanical_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        data["mechanical_fit"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"mechanical fit audit is missing or invalid: {exc}")
    try:
        panel_assembly = json.loads(args.panel_assembly_audit.read_text())
        panel_assembly_issues = list(panel_assembly.get("issues", []))
        if not panel_assembly.get("passed") and not panel_assembly_issues:
            panel_assembly_issues.append("panel assembly audit did not pass")
        if panel_assembly.get("unit_board_sha256") != board_sha:
            panel_assembly_issues.append("panel assembly audit is not bound to the released board SHA-256")
        panel_sha = sha256(panel_path)
        if panel_assembly.get("panel_board_sha256") != panel_sha:
            panel_assembly_issues.append("panel assembly audit is not bound to the released panel SHA-256")
        data["panel_assembly"] = {
            "passed": not panel_assembly_issues,
            "issues": panel_assembly_issues,
            "report": f"reports/{args.panel_assembly_audit.name}",
            "sha256": sha256(args.panel_assembly_audit),
        }
        hard.extend(f"panel assembly audit: {issue}" for issue in panel_assembly_issues)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        panel_sha = ""
        data["panel_assembly"] = {"passed": False, "issues": [str(exc)]}
        hard.append(f"panel assembly audit is missing or invalid: {exc}")
    manifest_sha = sha256(args.stack_manifest)
    assembly_files = sorted((args.assembly_dir / "B-4n7-0p8").glob("*.csv"))
    assembly_hashes = {
        f"assembly/B-4n7-0p8/{path.name}": sha256(path) for path in assembly_files
    }
    expected_assembly_names = {
        f"assembly/B-4n7-0p8/{stem}.csv"
        for stem in ("panel-bom", "panel-centroid")
    }
    if set(assembly_hashes) != expected_assembly_names:
        hard.append("first-article assembly CSV set differs")
    netlist_sha = sha256(netlist_path) if netlist_path.is_file() else ""
    required_approval = approval_contract(
        board_sha, panel_sha, manifest_sha, netlist_sha, assembly_hashes,
        manifest, sorted(expected_drills)
    )
    data["required_vendor_approval"] = required_approval
    dfm = read_approval(args.vendor_dfm_approval, required_approval)
    if dfm:
        blockers.append("vendor_dfm_approval")
    data["source"] = {"board_sha256": board_sha, "manifest_sha256": manifest_sha}
    data["vendor_dfm"] = {"passed": not dfm, "issue_codes": dfm}
    data["dry_run"] = args.dry_run
    data["hard_failures"] = hard
    production_blockers = list(blockers)
    production_blockers.extend([
        "firmware_artifact_sha256",
        "u1_active_order_code_migration",
        "first_article_functional_test",
        "rf_vna_ota_2402_2440_2480_mhz",
        "usb_iec_61000_4_2",
    ])
    first_article_orderable = not args.dry_run and not hard and not blockers
    data["release_blockers"] = blockers
    data["first_article_orderable"] = first_article_orderable
    data["production_release_blockers"] = production_blockers
    data["production_orderable"] = False
    # Backward-compatible meaning: an order is permitted only for first articles.
    data["orderable"] = first_article_orderable
    data["release_class"] = "FIRST_ARTICLE" if first_article_orderable else "DFM_REVIEW"
    args.out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 1 if hard or (blockers and not args.dry_run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
