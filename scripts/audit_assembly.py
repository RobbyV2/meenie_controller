#!/usr/bin/env python3
"""Cross-check normalized BOM/CPL variants against raw KiCad exports."""

import argparse, csv, json, pathlib, re, zipfile


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


def xlsx_rows(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "xl/worksheets/sheet1.xml" not in names:
            raise ValueError(f"{path}: sheet1 missing")
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
    return len(re.findall(r"<row\b", xml))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assembly", required=True, type=pathlib.Path)
    parser.add_argument("--raw-bom", required=True, type=pathlib.Path)
    parser.add_argument("--panel-count", required=True, type=int)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    issues, variants = [], {}
    raw = rows(args.raw_bom)
    stale = sorted(row["Designator"] for row in raw
                   if not row.get("Manufacturer", "").strip() or not row.get("MPN", "").strip())
    for folder in sorted(path for path in args.assembly.iterdir() if path.is_dir()):
        try:
            ubom = rows(folder / "unit-bom.csv")
            pbom = rows(folder / "panel-bom.csv")
            ucpl = rows(folder / "unit-centroid.csv")
            pcpl = rows(folder / "panel-centroid.csv")
            urefs, prefs = bom_refs(ubom), bom_refs(pbom)
            ucpl_refs = {row["Designator"] for row in ucpl}
            pcpl_refs = {row["Designator"] for row in pcpl}
            if urefs != ucpl_refs:
                raise ValueError(f"unit BOM/CPL sets differ: {sorted(urefs ^ ucpl_refs)}")
            if prefs != pcpl_refs:
                raise ValueError(f"panel BOM/CPL sets differ: {sorted(prefs ^ pcpl_refs)[:10]}")
            expected_panel = {f"{ref}_{index:02d}" for ref in urefs for index in range(1, args.panel_count + 1)}
            if prefs != expected_panel:
                raise ValueError(f"panel references are not exact {args.panel_count}-fold unit references")
            for stem, count in (("unit-bom", len(ubom) + 1), ("panel-bom", len(pbom) + 1),
                                ("unit-centroid", len(ucpl) + 1), ("panel-centroid", len(pcpl) + 1)):
                actual = xlsx_rows(folder / f"{stem}.xlsx")
                if actual != count:
                    raise ValueError(f"{stem}.xlsx rows {actual} != CSV rows {count}")
            variants[folder.name] = {
                "passed": True,
                "unit_fitted": len(urefs),
                "unit_bom_lines": len(ubom),
                "panel_fitted": len(prefs),
                "panel_bom_lines": len(pbom),
            }
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            issues.append(f"{folder.name}: {exc}")
            variants[folder.name] = {"passed": False, "issue": str(exc)}
    result = {
        "passed": not issues and len(variants) == 4,
        "issues": issues + ([] if len(variants) == 4 else [f"expected 4 variants, found {len(variants)}"]),
        "variants": variants,
        "source_rows_missing_manufacturer_or_mpn": stale,
        "source_metadata_disposition": (
            "C1/C2/Y1 are source-complete and release-gated. Listed legacy rows are normalized only through the "
            "curated LCSC-to-manufacturer/MPN map; populate them in the schematic in a future metadata-only cleanup."
        ),
    }
    result["passed"] = not result["issues"]
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
