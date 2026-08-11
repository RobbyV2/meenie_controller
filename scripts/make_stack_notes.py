#!/usr/bin/env python3
import argparse, csv, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "panel"))
from product import load_manifest
import sexp


def unq(value):
    return sexp.unq(value) if isinstance(value, str) else value


def child_value(node, name, default=None):
    item = sexp.kid(node, name)
    return unq(item[1]) if item and len(item) >= 2 else default


def board_stack(board):
    tree = sexp.parse(board.read_text())
    setup = sexp.kid(tree, "setup")
    stackup = sexp.kid(setup, "stackup") if setup else None
    if stackup is None:
        raise SystemExit("board has no stackup")
    rows = []
    for item in sexp.kids(stackup, "layer"):
        name = unq(item[1])
        kind = child_value(item, "type", "")
        if kind != "copper" and not name.startswith("dielectric "):
            continue
        thickness = float(child_value(item, "thickness", -1))
        if thickness <= 0:
            raise SystemExit(f"stack layer {name} lacks positive thickness")
        material = child_value(item, "material", "")
        detail = material
        if kind == "copper":
            detail = "finished copper"
        rows.append((name, kind, thickness, detail))
    copper = [row[0] for row in rows if row[1] == "copper"]
    expected = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"]
    if copper != expected or len(rows) != 15:
        raise SystemExit(f"unexpected 8L stack rows/copper: {len(rows)} {copper!r}")
    general = sexp.kid(tree, "general")
    declared = float(child_value(general, "thickness", -1))
    total = sum(row[2] for row in rows)
    if abs(total - declared) > 1e-6:
        raise SystemExit(f"stack row sum {total:.6f} differs from board thickness {declared:.6f}")
    return rows, total


def notes(manifest, stack, total):
    central = manifest.get("central_buried_transitions", [])
    geoms = sorted({(tier["pad_mm"], tier["hole_mm"]) for item in manifest["stacks"] for tier in item["tiers"]})
    geom_text = ", ".join(f"{pad:.3f}/{hole:.3f} mm" for pad, hole in geoms)
    dielectrics = [row[2] for row in stack if row[1] != "copper"]
    central_text = (
        f"- Central In3.Cu-In4.Cu buried program present: {len(central)} transition(s).\n"
        "- Fabrication and release require this operation in the signed DFM approval."
        if central else
        "- No central In3.Cu-In4.Cu buried operation is present or permitted."
    )
    return f"""CONTROLLED IMPEDANCE AND 8-LAYER 3+2+3 HDI NOTE

Vendor/order path: NextPCB Advanced PCB with written engineering review.
Nominal order thickness: 1.0 mm; exact released KiCad stack sum {total:.3f} mm.
Material and resin content: use the exact attached stack CSV as the design request; vendor must return a pressed construction for written approval. Finish ENIG, 35 um outer and 17.5 um inner copper.

RF_50
- B.Cu referenced to continuous adjacent In6.Cu GND.
- Artwork nominal: 0.115 mm width, at least 0.310 mm same-side copper setback, soldermask retained.
- Target 50 ohm single-ended, +/-10% maximum.
- Field-solve using the vendor's proposed pressed {dielectrics[-1]:.3f} mm B.Cu-In6 dielectric, resin content, finished copper, and mask. No pre-DFM impedance number is asserted.
- Adjust RF_50 only after written customer approval; do not change ANT_FEED or matching geometry.
- Add a B.Cu/In6 coupon when required and supply the lot TDR result.

SEQUENTIAL HDI / VIPPO
- Construction: 3+2+3 with laser pairs F-In1, In1-In2, In2-In3, In4-In5, In5-In6, In6-B.
- Released adjacent-tier dielectric thicknesses: {', '.join(f'{value:.3f}' for value in dielectrics)} mm.
- Manifest-observed laser geometry: {geom_text}.
- Every laser tier must be copper filled and planarized before the next lamination.
- Copper fill, cap, and planarize every via-in-pad feature; maximum cap dimple 10 um.
- Microsection both face stacks and X-ray U1 on the first article.
{central_text}
- No stack, via geometry, impedance, or central operation is approved by this note alone; signed vendor DFM must bind the released board and manifest SHA-256 values.
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--board", required=True, type=pathlib.Path)
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    stack, total = board_stack(args.board)
    with (out / "nextpcb-stackup.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Order", "Layer/build-up", "Type", "Thickness mm", "Function/note"])
        for index, row in enumerate(stack, 1):
            writer.writerow([index, *row])
        writer.writerow(["", "STACK SUM", "", f"{total:.4f}", "exact released KiCad stack; quote nominal 1.0 mm"])
    (out / "IMPEDANCE-HDI-NOTES.txt").write_text(notes(manifest, stack, total))
    print(f"stack rows {len(stack)} sum {total:.4f} mm")


if __name__ == "__main__":
    main()
