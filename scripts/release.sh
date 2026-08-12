#!/usr/bin/env bash
set -Eeuo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=${MEENIE_ROOT:-$(cd "$HERE/.." && pwd -P)}
INPUT=${MEENIE_HDI_BOARD:-${1:-}}
OUT=${MEENIE_HDI_RELEASE_OUT:-$ROOT/.release-build}
FAB=${MEENIE_FAB_DIR:-$ROOT/fab}
MANIFEST=${MEENIE_STACK_MANIFEST:-}
DFM=${MEENIE_VENDOR_DFM_APPROVAL:-}
DRY_RUN=${MEENIE_DRY_RUN:-0}
K=${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}
PY=${PYTHON:-python3}
KPY=${KICAD_PYTHON:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3}
LAYERS='F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,In5.Cu,In6.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts'

die() { echo "FAIL  $*" >&2; exit 1; }
kc() { "$K" "$@" 2> >(grep -v '^Fontconfig warning' >&2); }
case "$DRY_RUN" in 0|1) ;; *) die "MEENIE_DRY_RUN must be 0 or 1" ;; esac
[ -n "$INPUT" ] || die "board path argument or MEENIE_HDI_BOARD is required"
[ -f "$INPUT" ] || die "missing board $INPUT"
INPUT=$(cd "$(dirname "$INPUT")" && printf '%s/%s\n' "$PWD" "$(basename "$INPUT")")
SRC_DIR=$(dirname "$INPUT")
SRC_STEM=$(basename "$INPUT" .kicad_pcb)
[ -n "$MANIFEST" ] || MANIFEST="$SRC_DIR/stack-manifest.json"
[ -f "$MANIFEST" ] || die "reviewed stack manifest is required: $MANIFEST"
MANIFEST=$(cd "$(dirname "$MANIFEST")" && printf '%s/%s\n' "$PWD" "$(basename "$MANIFEST")")
for ext in kicad_pcb kicad_pro kicad_sch; do
  [ -f "$SRC_DIR/$SRC_STEM.$ext" ] || die "missing companion $SRC_DIR/$SRC_STEM.$ext"
done

mkdir -p "$OUT/stage/project" "$OUT/stage/lib" "$OUT/fab" "$OUT/reports" "$OUT/assembly"
OUT=$(cd "$OUT" && pwd -P)
mkdir -p "$FAB"
FAB=$(cd "$FAB" && pwd -P)
inside() { case "$2" in "$1"/*) echo yes ;; *) echo no ;; esac; }
[ "$(inside "$ROOT" "$OUT")" = yes ] || die "build directory must remain below $ROOT: $OUT"
[ "$(inside "$ROOT" "$FAB")" = yes ] || die "fab directory must remain below $ROOT: $FAB"
[ "$(inside "$OUT" "$FAB")" = no ] || die "fab directory must not sit inside the build directory: $FAB"
[ "$(inside "$FAB" "$OUT")" = no ] || die "build directory must not sit inside the fab directory: $OUT"
for dir in "$OUT/stage/project" "$OUT/stage/lib" "$OUT/fab" \
           "$OUT/reports" "$OUT/assembly"; do
  find "$dir" -mindepth 1 -depth -delete
done
mkdir -p "$OUT/fab/board/gerbers" "$OUT/fab/panel/gerbers" \
  "$OUT/reports/mech-gerbers"
for ext in kicad_pcb kicad_pro kicad_sch; do
  cp "$SRC_DIR/$SRC_STEM.$ext" "$OUT/stage/project/meenie.$ext"
done
for table in fp-lib-table sym-lib-table; do
  [ ! -f "$SRC_DIR/$table" ] || cp "$SRC_DIR/$table" "$OUT/stage/project/$table"
done
MANIFEST_DIR=$(dirname "$MANIFEST")
RULE_NAME=$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["rules"])' "$MANIFEST")
[ -f "$MANIFEST_DIR/$RULE_NAME" ] || die "manifest rules missing: $MANIFEST_DIR/$RULE_NAME"
cp "$MANIFEST_DIR/$RULE_NAME" "$OUT/stage/project/meenie.kicad_dru"
cp "$MANIFEST" "$OUT/stage/project/stack-manifest.json"
if [ -d "$SRC_DIR/../lib" ]; then
  cp -R "$SRC_DIR/../lib/." "$OUT/stage/lib/"
fi
if [ -d "$ROOT/lib/3dmodels" ] && [ ! -d "$OUT/stage/lib/3dmodels" ]; then
  cp -R "$ROOT/lib/3dmodels" "$OUT/stage/lib/3dmodels"
fi

BRD="$OUT/stage/project/meenie.kicad_pcb"
SCH="$OUT/stage/project/meenie.kicad_sch"
STACK="$OUT/stage/project/stack-manifest.json"
RAW_BOM="$OUT/stage/raw-bom.csv"
PANEL_POS="$OUT/stage/panel-centroid.csv"
PANEL="$OUT/fab/meenie-panel.kicad_pcb"
MECH="$OUT/reports/mech.kicad_pcb"
BG="$OUT/fab/board/gerbers"
PG="$OUT/fab/panel/gerbers"
MG="$OUT/reports/mech-gerbers"

"$PY" -c 'import hashlib,json,os,pathlib,sys
root=sys.argv[1]
data={os.path.relpath(p,root):hashlib.sha256(open(p,"rb").read()).hexdigest() for p in sys.argv[3:]}
pathlib.Path(sys.argv[2]).write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")' \
  "$ROOT" "$OUT/reports/source-sha256.json" \
  "$INPUT" "$SRC_DIR/$SRC_STEM.kicad_sch" "$SRC_DIR/$SRC_STEM.kicad_pro" \
  "$MANIFEST_DIR/$RULE_NAME" "$MANIFEST"
echo "0  source $(shasum -a 256 "$BRD" | cut -d' ' -f1)"

echo "1  native DRC, exact stack gate, ERC"
set +e
( cd "$OUT/stage/project" && kc pcb drc --severity-all --exit-code-violations \
    --schematic-parity --format json -o "$OUT/reports/drc.json" "$BRD" )
DRC_RC=$?
set -e
case "$DRC_RC" in 0|5) ;; *) die "kicad-cli DRC exited $DRC_RC" ;; esac
BEFORE=$(shasum -a 256 "$BRD" | cut -d' ' -f1)
"$PY" "$HERE/stack/validate_stacks.py" "$OUT/stage/project" --manifest "$STACK" \
  --drc-json "$OUT/reports/drc.json"
AFTER=$(shasum -a 256 "$BRD" | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] || die "validation mutated the staged board"
"$PY" - "$OUT/reports/drc.json" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
active = [item for item in report.get("violations", []) if not item.get("excluded")]
unconnected = report.get("unconnected_items", [])
parity = report.get("schematic_parity", [])
issues = []
if active: issues.append(f"{len(active)} active DRC violation(s)")
if unconnected: issues.append(f"{len(unconnected)} unconnected item(s)")
if parity: issues.append(f"{len(parity)} schematic parity issue(s)")
if issues: raise SystemExit("FAIL: " + "; ".join(issues))
PY
"$KPY" "$HERE/validate_rf_reference.py" --board "$BRD" \
  --out "$OUT/reports/rf-reference.json"
"$KPY" "$HERE/validate_usb_geometry.py" --board "$BRD" --out "$OUT/reports/usb-interface.json"
"$KPY" "$HERE/validate_power_geometry.py" --board "$BRD" \
  --out "$OUT/reports/power-geometry.json"
"$KPY" "$HERE/validate_hfxo.py" --board "$BRD" --out "$OUT/reports/hfxo.json"
"$KPY" "$HERE/validate_mechanical_fit.py" --board "$BRD" --out "$OUT/reports/mechanical-fit.json"
kc sch erc --severity-all --format json -o "$OUT/reports/erc.json" "$SCH" || true
[ -s "$OUT/reports/erc.json" ] || die "ERC report missing"
"$PY" - "$OUT/reports/erc.json" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
active = [item for item in report.get("violations", []) if not item.get("excluded")]
for sheet in report.get("sheets", []):
    active.extend(item for item in sheet.get("violations", []) if not item.get("excluded"))
if active: raise SystemExit(f"FAIL: {len(active)} active ERC issue(s)")
PY
kc sch export netlist --format kicadxml -o "$OUT/reports/netlist.xml" "$SCH"
"$PY" "$HERE/audit_circuit.py" --netlist "$OUT/reports/netlist.xml" \
  --out "$OUT/reports/circuit-audit.json"

echo "2  unit manufacturing data"
kc pcb export gerbers -o "$BG" -l "$LAYERS" --subtract-soldermask --check-zones "$BRD"
kc pcb export drill -o "$BG" --format excellon --excellon-units mm --excellon-separate-th \
  "$BRD"
kc pcb export ipcd356 -o "$OUT/fab/netlist.ipc" "$BRD"
kc sch export bom -o "$RAW_BOM" \
  --fields 'Reference,Value,Footprint,${QUANTITY},LCSC Part,Manufacturer,MPN,DNP' \
  --labels 'Designator,Value,Footprint,Quantity,LCSC Part,Manufacturer,MPN,DNP' \
  --exclude-dnp "$SCH"
"$PY" "$HERE/validate_source_contract.py" --board "$BRD" \
  --bom "$RAW_BOM" --out "$OUT/reports/source-contract.json"

echo "3  stackup data"
"$PY" "$HERE/release_data.py" stackup --board "$BRD" --out "$OUT/fab/stackup.csv"
cp "$STACK" "$OUT/fab/stack-manifest.json"

PANEL_GRID=$(MEENIE_ROOT="$ROOT" MEENIE_BRD="$BRD" "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import panel; print("%d %d %d" % (panel.COLS, panel.ROWS, panel.BOARDS))' "$HERE/panel")
read -r PANEL_COLS PANEL_ROWS PANEL_COUNT <<<"$PANEL_GRID"

echo "4  $PANEL_COLS x $PANEL_ROWS customer panel, $PANEL_COUNT boards"
for command in mech build mechonly; do
  case "$command" in
    mech) args=(--mech) ;;
    build) args=("$PANEL") ;;
    mechonly) args=(--mech-only "$MECH") ;;
  esac
  MEENIE_ROOT="$ROOT" MEENIE_BRD="$BRD" MEENIE_PANEL="$PANEL" \
  MEENIE_PANEL_MECH="$MECH" "$KPY" "$HERE/panel/panel.py" "${args[@]}"
done
find "$OUT/fab" -maxdepth 1 -type f -name '*.kicad_prl' -delete
PANEL_REPRO="$OUT/reports/panel-reproducibility.kicad_pcb"
MEENIE_ROOT="$ROOT" MEENIE_BRD="$BRD" MEENIE_PANEL="$PANEL" \
  MEENIE_PANEL_MECH="$MECH" "$KPY" "$HERE/panel/panel.py" "$PANEL_REPRO" > /dev/null
PANEL_SHA=$(shasum -a 256 "$PANEL" | cut -d' ' -f1)
PANEL_REPRO_SHA=$(shasum -a 256 "$PANEL_REPRO" | cut -d' ' -f1)
[ "$PANEL_SHA" = "$PANEL_REPRO_SHA" ] || die \
  "panel generation is not reproducible: $PANEL_SHA != $PANEL_REPRO_SHA"
rm "$PANEL_REPRO"
"$KPY" "$HERE/validate_panel_assembly.py" --board "$BRD" --panel "$PANEL" \
  --out "$OUT/reports/panel-assembly.json"
kc pcb export gerbers -o "$PG" -l "$LAYERS" --subtract-soldermask "$PANEL"
kc pcb export drill -o "$PG" --format excellon --excellon-units mm --excellon-separate-th \
  "$PANEL"
kc pcb export pos -o "$PANEL_POS" --format csv --units mm \
  --side both --exclude-dnp "$PANEL"
kc pcb export gerbers -o "$MG" -l "$LAYERS" --subtract-soldermask "$MECH"
kc pcb export drill -o "$MG" --format excellon --excellon-units mm --excellon-separate-th "$MECH"

echo "5  assembly populations"
"$PY" "$HERE/make_assembly.py" --bom "$RAW_BOM" \
  --panel-pos "$PANEL_POS" \
  --panel-count "$PANEL_COUNT" --out "$OUT/assembly"
"$PY" "$HERE/audit_assembly.py" --assembly "$OUT/assembly" --panel-count "$PANEL_COUNT" \
  --raw-bom "$RAW_BOM" \
  --panel-pos "$PANEL_POS" \
  --out "$OUT/reports/assembly-audit.json"
if [ "${MEENIE_SKIP_LIVE_STOCK:-0}" != 1 ]; then
  "$PY" "$HERE/audit_stock.py" --assembly "$OUT/assembly" --out "$OUT/reports/stock-audit.json"
else
  [ "$DRY_RUN" = 1 ] || die "live stock audit cannot be skipped for a production release"
  "$PY" - "$OUT/reports/stock-audit.json" <<'PY'
import datetime, json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "checked_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "passed": False,
    "issues": ["live stock audit was skipped"],
    "parts": [],
}, indent=2, sort_keys=True) + "\n")
PY
fi

echo "6  exact panel correspondence"
MEENIE_ROOT="$ROOT" MEENIE_BRD="$BRD" MEENIE_PANEL="$PANEL" \
MEENIE_PANEL_MECH="$MECH" MEENIE_BOARD_PREFIX=meenie MEENIE_PANEL_PREFIX=meenie-panel \
MEENIE_MECH_PREFIX=mech MEENIE_BGERB="$BG" MEENIE_MGERB="$MG" MEENIE_STACK_MANIFEST="$STACK" \
  "$PY" "$HERE/panel/verify.py" "$PG"
"$PY" - "$OUT/reports/panel-audit.json" "$PANEL_SHA" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "passed": True,
    "byte_identical_reproduction": True,
    "gerber_drill_correspondence": True,
    "panel_sha256": sys.argv[2],
}, indent=2, sort_keys=True) + "\n")
PY

echo "7  mouse-bite clearance"
MEENIE_BRD="$BRD" MEENIE_PANEL_PREFIX=meenie-panel MEENIE_PGERB="$PG" \
  "$PY" - "$HERE/panel" "$OUT/reports/mouse-bite-clearance.json" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import biteclr
bad = []
rows = []
for radius, label in ((0.250, '0.500 mm drawn'), (0.300, '0.600 mm process envelope')):
    margin = biteclr.main(radius)
    rows.append({
        "label": label,
        "radius_mm": radius,
        "minimum_margin_mm": margin,
        "required_margin_mm": biteclr.COPPER_GAP,
        "margin_delta_mm": margin - biteclr.COPPER_GAP,
    })
    if margin < biteclr.COPPER_GAP - 1e-6:
        bad.append(label)
pathlib.Path(sys.argv[2]).write_text(json.dumps({
    "passed": not bad,
    "results": rows,
}, indent=2, sort_keys=True) + "\n")
raise SystemExit(1 if bad else 0)
PY

echo "8  release assertions"
validate=("$PY" "$HERE/validate_release.py" --board-dir "$OUT/stage/project" \
  --board-gerbers "$BG" --panel-gerbers "$PG" --panel-board "$PANEL" \
  --drc "$OUT/reports/drc.json" \
  --erc "$OUT/reports/erc.json" --stack-manifest "$STACK" \
  --circuit-audit "$OUT/reports/circuit-audit.json" \
  --rf-audit "$OUT/reports/rf-reference.json" \
  --usb-audit "$OUT/reports/usb-interface.json" \
  --power-audit "$OUT/reports/power-geometry.json" \
  --hfxo-audit "$OUT/reports/hfxo.json" \
  --mechanical-fit-audit "$OUT/reports/mechanical-fit.json" \
  --panel-assembly-audit "$OUT/reports/panel-assembly.json" \
  --assembly-dir "$OUT/assembly" \
  --stock-audit "$OUT/reports/stock-audit.json" \
  --out "$OUT/reports/release.json")
[ -z "$DFM" ] || validate+=(--vendor-dfm-approval "$DFM")
[ "$DRY_RUN" = 0 ] || validate+=(--dry-run)
MEENIE_BRD="$BRD" "${validate[@]}"
$PY - "$OUT/reports/release.json" "$OUT/fab/vendor-dfm-approval-template.json" <<'PY'
import hashlib, json, pathlib, sys
release = json.loads(pathlib.Path(sys.argv[1]).read_text())
template = dict(release["required_vendor_approval"])
template.update({
    "vendor_signer_name": "",
    "vendor_signer_email": "",
    "vendor_quote_number": "",
    "vendor_signed_at": "",
    "rf_50_field_solved_impedance_ohms": None,
    "rf_50_field_solve_report_reference": "",
    "vendor_pressed_stack_reference": "",
    "vendor_material_reference": "",
    "vendor_three_tier_stack_process_qualification_reference": "",
    "vendor_microsection_reference": "",
    "vendor_stencil_process_reference": "",
    "vendor_cam_checkplot_reference": "",
    "vendor_edge_clearance_inspection_reference": "",
    "vendor_ipc_class_3_certificate_reference": "",
    "vendor_evidence_sha256": {},
})
template_path = pathlib.Path(sys.argv[2])
template_path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n")
release["required_vendor_approval"] = {
    "path": "fab/vendor-dfm-approval-template.json",
    "sha256": hashlib.sha256(template_path.read_bytes()).hexdigest(),
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(release, indent=2, sort_keys=True) + "\n")
PY
FIRST_ARTICLE_ORDERABLE=$($PY -c 'import json,sys; print(1 if json.load(open(sys.argv[1]))["first_article_orderable"] else 0)' "$OUT/reports/release.json")

echo "9  DFM review archive"
cp "$BRD" "$OUT/fab/meenie-board.kicad_pcb"
find "$OUT/fab" -type f -name '*.kicad_prl' -delete
( cd "$OUT" && "$PY" - <<'PY'
import hashlib, json, pathlib

root = pathlib.Path(".")
targets = [
    root / "fab/meenie-board.kicad_pcb",
    root / "fab/meenie-panel.kicad_pcb",
    root / "fab/stackup.csv",
    root / "fab/stack-manifest.json",
    root / "fab/netlist.ipc",
    root / "fab/vendor-dfm-approval-template.json",
]
for directory in (root / "fab/board", root / "fab/panel", root / "assembly/B-4n7-0p8"):
    targets.extend(path for path in directory.rglob("*") if path.is_file())
targets.extend((root / "reports").glob("*.json"))
targets.extend((root / "reports").glob("*.csv"))
manifest = root / "fab/sha256sums.json"
targets = sorted({path for path in targets if path != manifest}, key=lambda path: path.as_posix())
manifest.write_text(json.dumps({
    path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in targets
}, indent=2, sort_keys=True) + "\n")
PY
)
( cd "$OUT" && zip -qX -r "$OUT/fab/dfm-review.zip" \
    fab/meenie-board.kicad_pcb fab/meenie-panel.kicad_pcb \
    fab/board fab/panel fab/stackup.csv fab/stack-manifest.json \
    fab/netlist.ipc fab/vendor-dfm-approval-template.json fab/sha256sums.json \
    assembly/B-4n7-0p8 reports/*.json reports/*.csv )
"$PY" - "$OUT/fab/dfm-review.zip" <<'PY'
import hashlib, json, pathlib, sys, zipfile

archive_path = pathlib.Path(sys.argv[1])
required = {
    "fab/meenie-board.kicad_pcb",
    "fab/meenie-panel.kicad_pcb",
    "fab/sha256sums.json",
    "fab/netlist.ipc",
    "fab/stackup.csv",
    "fab/stack-manifest.json",
    "fab/vendor-dfm-approval-template.json",
    "reports/release.json",
    "fab/board/gerbers/meenie-F_Cu.gtl",
    "fab/panel/gerbers/meenie-panel-F_Cu.gtl",
    "assembly/B-4n7-0p8/panel-bom.csv",
    "assembly/B-4n7-0p8/panel-centroid.csv",
}
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    bad = []
    if len(names) != len(set(names)):
        bad.append("duplicate member names")
    for name in names:
        pure = pathlib.PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            bad.append(f"unsafe member {name!r}")
        elif pure.parts[0] not in {"fab", "assembly", "reports"}:
            bad.append(f"unexpected member root {name!r}")
        elif pure.suffix.lower() in {
            ".md", ".txt", ".htm", ".html", ".kicad_prl", ".log", ".pyc", ".tmp"
        } or "notes" in pure.parts or "__pycache__" in pure.parts:
            bad.append(f"forbidden member {name!r}")
    bad.extend(f"missing required member {name!r}" for name in sorted(required - set(names)))
    corrupt = archive.testzip()
    if corrupt:
        bad.append(f"CRC failure at {corrupt!r}")
    manifest = json.loads(archive.read("fab/sha256sums.json"))
    covered = set(manifest)
    for name, expected in manifest.items():
        if name not in names:
            bad.append(f"checksum member missing from archive {name!r}")
        elif hashlib.sha256(archive.read(name)).hexdigest() != expected:
            bad.append(f"checksum mismatch for {name!r}")
    payload = {
        name for name in names
        if not name.endswith("/") and name != "fab/sha256sums.json"
    }
    for name in sorted(payload - covered):
        bad.append(f"archive payload is not checksum-covered {name!r}")
if bad:
    raise SystemExit("FAIL: DFM review ZIP: " + "; ".join(bad))
print(f"PASS: DFM ZIP paths, required native sources, {len(covered)} checksums, and CRCs")
PY
rm "$OUT/fab/sha256sums.json"

if [ "$FIRST_ARTICLE_ORDERABLE" = 1 ]; then
  echo "9a first-article upload archives"
  cp "$DFM" "$OUT/fab/vendor-dfm-approval.json"
  cp "$OUT/reports/release.json" "$OUT/fab/release.json"
  ( cd "$OUT/fab" && zip -qX -r first-article-board-upload.zip \
      board netlist.ipc stackup.csv stack-manifest.json vendor-dfm-approval.json release.json )
  ( cd "$OUT/fab" && zip -qX -r first-article-panel-upload.zip \
      panel netlist.ipc stackup.csv stack-manifest.json vendor-dfm-approval.json release.json )
  rm "$OUT/fab/release.json"
  ( cd "$OUT/assembly/B-4n7-0p8" && zip -qX -r "$OUT/fab/first-article-B-4n7-0p8-assembly.zip" . )
  "$PY" - "$OUT/fab/first-article-board-upload.zip" \
    "$OUT/fab/first-article-panel-upload.zip" \
    "$OUT/fab/first-article-B-4n7-0p8-assembly.zip" <<'PY'
import pathlib, sys, zipfile

board, panel, assembly = map(pathlib.Path, sys.argv[1:])

def audit(path, allowed_roots, required_files):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        bad = []
        if len(names) != len(set(names)):
            bad.append("duplicate member names")
        for name in names:
            pure = pathlib.PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                bad.append(f"unsafe member {name!r}")
            elif pure.parts[0] not in allowed_roots:
                bad.append(f"unexpected member root {name!r}")
            elif pure.suffix.lower() in {
                ".md", ".txt", ".htm", ".html", ".kicad_prl", ".log", ".pyc", ".tmp"
            } or "notes" in pure.parts or "__pycache__" in pure.parts:
                bad.append(f"forbidden member {name!r}")
        missing = [name for name in required_files if name not in names]
        bad.extend(f"missing required member {name!r}" for name in missing)
        corrupt = archive.testzip()
        if corrupt:
            bad.append(f"CRC failure at {corrupt!r}")
    if bad:
        raise SystemExit(f"FAIL: {path.name}: " + "; ".join(bad))

audit(board, {"board", "netlist.ipc", "stackup.csv", "stack-manifest.json", "vendor-dfm-approval.json", "release.json"},
      ("netlist.ipc", "stackup.csv",
       "vendor-dfm-approval.json", "release.json"))
audit(panel, {"panel", "netlist.ipc", "stackup.csv", "stack-manifest.json", "vendor-dfm-approval.json", "release.json"},
      ("netlist.ipc", "stackup.csv",
       "vendor-dfm-approval.json", "release.json"))
audit(assembly, {"panel-bom.csv", "panel-centroid.csv"},
      ("panel-bom.csv", "panel-centroid.csv"))
print("PASS: first-article ZIP paths, members, uniqueness, and CRCs")
PY
else
  find "$OUT/fab" -maxdepth 1 -name 'first-article-*.zip' -delete
fi

echo "10 adopt into $(basename "$FAB")"
NEW="$OUT/adopt"
mkdir -p "$NEW"
find "$NEW" -mindepth 1 -depth -delete
cp -R "$OUT/fab/." "$NEW/"
mkdir -p "$NEW/reports"
find "$OUT/reports" -maxdepth 1 -type f \( -name '*.json' -o -name '*.csv' \) \
  -exec cp {} "$NEW/reports/" \;
cp -R "$OUT/assembly" "$NEW/assembly"

find "$NEW" -type f \( -name '*.kicad_prl' -o -name '*.pyc' -o -name '.DS_Store' \) -delete
find "$NEW" -type d -name '__pycache__' -depth -delete
"$PY" - "$NEW" <<'PY'
import pathlib, sys

root = pathlib.Path(sys.argv[1])
allowed = {
    ".csv", ".drl", ".g1", ".g2", ".g3", ".g4", ".g5", ".g6",
    ".gbl", ".gbo", ".gbp", ".gbrjob", ".gbs", ".glb", ".gm1",
    ".gtl", ".gto", ".gtp", ".gts", ".ipc", ".json", ".kicad_pcb",
    ".png", ".zip",
}
bad = [str(path.relative_to(root)) for path in root.rglob("*")
       if path.is_file() and path.suffix.lower() not in allowed]
if bad:
    raise SystemExit("FAIL: forbidden release artifacts: " + ", ".join(sorted(bad)))
PY

echo "   generated files only; no prior release content carried forward"

for required in meenie-board.kicad_pcb meenie-panel.kicad_pcb board/gerbers panel/gerbers netlist.ipc stackup.csv \
                stack-manifest.json vendor-dfm-approval-template.json dfm-review.zip \
                assembly/B-4n7-0p8/panel-bom.csv assembly/B-4n7-0p8/panel-centroid.csv \
                reports/release.json reports/drc.json reports/erc.json \
                reports/source-sha256.json reports/stock-audit.json reports/usb-interface.json \
                reports/power-geometry.json reports/hfxo.json reports/mechanical-fit.json \
                reports/panel-audit.json reports/panel-assembly.json \
                reports/source-contract.json reports/rf-reference.json \
                reports/mouse-bite-clearance.json; do
  [ -e "$NEW/$required" ] || die "adoption tree incomplete: $required"
done
"$PY" "$HERE/release_data.py" inventory --root "$NEW"
[ -s "$NEW/reports/artifact-inventory.json" ] || die "adoption inventory missing"

PREV="$FAB.previous.$$"
[ ! -d "$FAB" ] || mv "$FAB" "$PREV"
if ! mv "$NEW" "$FAB"; then
  [ ! -d "$PREV" ] || mv "$PREV" "$FAB"
  die "adoption swap failed; $(basename "$FAB") left untouched"
fi
[ ! -e "$PREV" ] || find "$PREV" -depth -delete
find "$OUT" -depth -delete

if [ "$FIRST_ARTICLE_ORDERABLE" = 1 ]; then
  echo "PASS  exact 8-layer first-article package adopted into $(basename "$FAB"); production qualification remains mandatory"
else
  echo "PASS  DFM review package adopted into $(basename "$FAB"); release.json lists blockers and no production upload archive exists"
fi
