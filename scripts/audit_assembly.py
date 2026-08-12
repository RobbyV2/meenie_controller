#!/usr/bin/env python3
"""Cross-check the first-article panel BOM/CPL against raw KiCad exports."""

import argparse, csv, json, pathlib


def rows(path):
    return list(csv.DictReader(path.open(newline="")))


def bom_refs(data):
    refs = []
    for row in data:
        names = [item for item in row["Designator"].split(",") if item]
        refs.extend(names)
        if int(row["Quantity"]) != len(names):
            raise ValueError(f"{row['Designator']}: quantity does not equal designator count")
        for field in ("Manufacturer", "MPN", "Distributor Part Number"):
            if not row[field].strip():
                raise ValueError(f"{row['Designator']}: blank normalized {field}")
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate BOM designator")
    return set(refs)


def raw_position(row):
    return {
        "Mid X (mm)": float(row["PosX"]),
        "Mid Y (mm)": float(row["PosY"]),
        "Layer": row["Side"].title(),
        "Rotation (deg CCW)": float(row["Rot"]),
        "Value": row["Val"],
        "Package": row["Package"],
    }


def verify_positions(normalized, raw, label):
    raw_by_ref = {row["Ref"]: raw_position(row) for row in raw}
    for row in normalized:
        ref = row["Designator"]
        if ref not in raw_by_ref:
            raise ValueError(f"{label} {ref}: absent from raw KiCad centroid")
        expected = raw_by_ref[ref]
        for field in ("Mid X (mm)", "Mid Y (mm)", "Rotation (deg CCW)"):
            if abs(float(row[field]) - expected[field]) > 1e-9:
                raise ValueError(f"{label} {ref}: {field} changed from raw KiCad centroid")
        for field in ("Layer", "Value", "Package"):
            if row[field] != expected[field]:
                raise ValueError(f"{label} {ref}: {field} changed from raw KiCad centroid")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assembly", required=True, type=pathlib.Path)
    parser.add_argument("--raw-bom", required=True, type=pathlib.Path)
    parser.add_argument("--panel-pos", required=True, type=pathlib.Path)
    parser.add_argument("--panel-count", required=True, type=int)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    issues = []
    raw = rows(args.raw_bom)
    raw_panel_pos = rows(args.panel_pos)
    stale = sorted(row["Designator"] for row in raw
                   if not row.get("Manufacturer", "").strip() or not row.get("MPN", "").strip())
    folders = sorted(path for path in args.assembly.iterdir() if path.is_dir())
    folder = args.assembly / "B-4n7-0p8"
    try:
        if folders != [folder]:
            raise ValueError(f"assembly populations differ: {[path.name for path in folders]}")
        pbom = rows(folder / "panel-bom.csv")
        pcpl = rows(folder / "panel-centroid.csv")
        prefs = bom_refs(pbom)
        pcpl_refs = {row["Designator"] for row in pcpl}
        if prefs != pcpl_refs:
            raise ValueError(f"panel BOM/CPL sets differ: {sorted(prefs ^ pcpl_refs)[:10]}")
        unit_refs = {row["Designator"] for row in raw}
        expected_panel = {f"{ref}_{index:02d}" for ref in unit_refs
                          for index in range(1, args.panel_count + 1)}
        if prefs != expected_panel:
            raise ValueError(f"panel references are not exact {args.panel_count}-fold source references")
        verify_positions(pcpl, raw_panel_pos, "panel")
    except (OSError, KeyError, ValueError) as exc:
        issues.append(str(exc))
    result = {
        "passed": not issues,
        "issues": issues,
        "population": "B-4n7-0p8",
        "panel_fitted": 0 if issues else len(prefs),
        "panel_bom_lines": 0 if issues else len(pbom),
        "source_rows_missing_manufacturer_or_mpn": stale,
    }
    if stale:
        result["issues"].append(f"source BOM rows lack Manufacturer or MPN: {stale}")
    result["passed"] = not result["issues"]
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
