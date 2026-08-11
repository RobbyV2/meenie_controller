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

mkdir -p "$OUT/stage/project" "$OUT/stage/lib" "$OUT/fab/board/gerbers" \
  "$OUT/fab/panel/gerbers" "$OUT/fab/notes" "$OUT/reports/mech-gerbers" "$OUT/3d" "$OUT/assembly"
OUT=$(cd "$OUT" && pwd -P)
mkdir -p "$FAB"
FAB=$(cd "$FAB" && pwd -P)
inside() { case "$2" in "$1"/*) echo yes ;; *) echo no ;; esac; }
[ "$(inside "$ROOT" "$OUT")" = yes ] || die "build directory must remain below $ROOT: $OUT"
[ "$(inside "$ROOT" "$FAB")" = yes ] || die "fab directory must remain below $ROOT: $FAB"
[ "$(inside "$OUT" "$FAB")" = no ] || die "fab directory must not sit inside the build directory: $FAB"
[ "$(inside "$FAB" "$OUT")" = no ] || die "build directory must not sit inside the fab directory: $OUT"
for dir in "$OUT/stage/project" "$OUT/stage/lib" "$OUT/fab/board/gerbers" \
           "$OUT/fab/panel/gerbers" "$OUT/fab/notes" "$OUT/reports" "$OUT/3d" "$OUT/assembly"; do
  find "$dir" -mindepth 1 -depth -delete
done
mkdir -p "$OUT/reports/mech-gerbers" "$OUT/3d"
for ext in kicad_pcb kicad_pro kicad_sch; do
  cp "$SRC_DIR/$SRC_STEM.$ext" "$OUT/stage/project/meenie.$ext"
done
[ ! -f "$SRC_DIR/$SRC_STEM.kicad_prl" ] || cp "$SRC_DIR/$SRC_STEM.kicad_prl" "$OUT/stage/project/meenie.kicad_prl"
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
PANEL="$OUT/fab/meenie-panel.kicad_pcb"
MECH="$OUT/reports/mech.kicad_pcb"
BG="$OUT/fab/board/gerbers"
PG="$OUT/fab/panel/gerbers"
MG="$OUT/reports/mech-gerbers"

"$PY" -c 'import hashlib,os,sys
root=sys.argv[1]
for p in sys.argv[2:]:
    print("%s  %s" % (hashlib.sha256(open(p,"rb").read()).hexdigest(), os.path.relpath(p, root)))' \
  "$ROOT" "$INPUT" "$SRC_DIR/$SRC_STEM.kicad_sch" "$SRC_DIR/$SRC_STEM.kicad_pro" \
  "$MANIFEST_DIR/$RULE_NAME" "$MANIFEST" > "$OUT/reports/source-sha256.txt"
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
  --drc-json "$OUT/reports/drc.json" | tee "$OUT/reports/stack-gate.txt"
AFTER=$(shasum -a 256 "$BRD" | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] || die "validation mutated the staged board"
"$PY" "$HERE/stack/run_product_tests.py" --project "$OUT/stage/project" \
  --drc-json "$OUT/reports/drc.json" --out "$OUT/reports/stack-negative-tests.json"
kc sch erc --severity-all --format json -o "$OUT/reports/erc.json" "$SCH" || true
[ -s "$OUT/reports/erc.json" ] || die "ERC report missing"

echo "2  unit manufacturing data"
kc pcb export gerbers -o "$BG" -l "$LAYERS" --subtract-soldermask --check-zones "$BRD"
kc pcb export drill -o "$BG" --format excellon --excellon-units mm --excellon-separate-th \
  --generate-report --report-path "$OUT/fab/board/drill-report.txt" "$BRD"
kc pcb export ipcd356 -o "$OUT/fab/board/netlist.ipc" "$BRD"
kc pcb export ipc2581 -o "$OUT/fab/board/ipc2581.xml" --version C --units mm \
  --bom-col-mfg-pn MPN --bom-col-mfg Manufacturer --bom-col-dist-pn 'LCSC Part' \
  --bom-col-dist LCSC "$BRD"
kc sch export bom -o "$OUT/fab/board/raw-bom.csv" \
  --fields 'Reference,Value,Footprint,${QUANTITY},LCSC Part,Manufacturer,MPN,DNP' \
  --labels 'Designator,Value,Footprint,Quantity,LCSC Part,Manufacturer,MPN,DNP' \
  --exclude-dnp "$SCH"
kc pcb export pos -o "$OUT/fab/board/raw-centroid.csv" --format csv --units mm \
  --side both --exclude-dnp "$BRD"
"$PY" "$HERE/validate_source_contract.py" --board "$BRD" \
  --bom "$OUT/fab/board/raw-bom.csv" --out "$OUT/reports/source-contract.json"

echo "3  stack and fabrication notes"
"$PY" "$HERE/make_stack_notes.py" --manifest "$STACK" --board "$BRD" --out "$OUT/fab/notes"

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
kc pcb export gerbers -o "$PG" -l "$LAYERS" --subtract-soldermask "$PANEL"
kc pcb export drill -o "$PG" --format excellon --excellon-units mm --excellon-separate-th \
  --generate-report --report-path "$OUT/fab/panel/drill-report.txt" "$PANEL"
kc pcb export ipcd356 -o "$OUT/fab/panel/netlist.ipc" "$PANEL"
kc pcb export ipc2581 -o "$OUT/fab/panel/ipc2581.xml" --version C --units mm "$PANEL"
kc pcb export pos -o "$OUT/fab/panel/raw-centroid.csv" --format csv --units mm \
  --side both --exclude-dnp "$PANEL"
kc pcb export gerbers -o "$MG" -l "$LAYERS" --subtract-soldermask "$MECH"
kc pcb export drill -o "$MG" --format excellon --excellon-units mm --excellon-separate-th "$MECH"

echo "5  assembly populations"
"$PY" "$HERE/make_assembly.py" --bom "$OUT/fab/board/raw-bom.csv" \
  --unit-pos "$OUT/fab/board/raw-centroid.csv" --panel-pos "$OUT/fab/panel/raw-centroid.csv" \
  --panel-count "$PANEL_COUNT" --out "$OUT/assembly" | tee "$OUT/reports/assembly-summary.csv"
"$PY" "$HERE/audit_assembly.py" --assembly "$OUT/assembly" --panel-count "$PANEL_COUNT" \
  --raw-bom "$OUT/fab/board/raw-bom.csv" --out "$OUT/reports/assembly-audit.json"

echo "6  exact panel correspondence"
MEENIE_ROOT="$ROOT" MEENIE_BRD="$BRD" MEENIE_PANEL="$PANEL" \
MEENIE_PANEL_MECH="$MECH" MEENIE_BOARD_PREFIX=meenie MEENIE_PANEL_PREFIX=meenie-panel \
MEENIE_MECH_PREFIX=mech MEENIE_BGERB="$BG" MEENIE_MGERB="$MG" MEENIE_STACK_MANIFEST="$STACK" \
  "$PY" "$HERE/panel/verify.py" "$PG" | tee "$OUT/reports/panel-correspondence.txt"

echo "7  mouse-bite clearance"
MEENIE_BRD="$BRD" MEENIE_PANEL_PREFIX=meenie-panel MEENIE_PGERB="$PG" \
  "$PY" - "$HERE/panel" <<'PY' | tee "$OUT/reports/mouse-bite-clearance.txt"
import sys
sys.path.insert(0, sys.argv[1])
import biteclr
bad = []
for radius, label in ((0.300, '0.600 mm drawn'), (0.350, '0.700 mm process envelope')):
    margin = biteclr.main(radius)
    print('%s worst %.4f mm margin %+.4f mm' % (label, margin, margin - 0.200))
    if margin < 0.200 - 1e-6:
        bad.append(label)
raise SystemExit(1 if bad else 0)
PY

echo "8  release assertions"
validate=("$PY" "$HERE/validate_release.py" --board-dir "$OUT/stage/project" \
  --board-gerbers "$BG" --panel-gerbers "$PG" --drc "$OUT/reports/drc.json" \
  --erc "$OUT/reports/erc.json" --stack-manifest "$STACK" \
  --out "$OUT/reports/release.json")
[ -z "$DFM" ] || validate+=(--vendor-dfm-approval "$DFM")
[ "$DRY_RUN" = 0 ] || validate+=(--dry-run)
MEENIE_BRD="$BRD" "${validate[@]}"
ORDERABLE=$($PY -c 'import json,sys; print(1 if json.load(open(sys.argv[1]))["orderable"] else 0)' "$OUT/reports/release.json")

if [ "$ORDERABLE" = 1 ]; then
  echo "9  upload archives"
  cp "$OUT/reports/source-sha256.txt" "$OUT/reports/stack-gate.txt" \
    "$OUT/reports/drc.json" "$OUT/reports/erc.json" "$OUT/reports/release.json" \
    "$OUT/fab/notes/"
  cp "$STACK" "$OUT/fab/notes/stack-manifest.json"
  cp "$DFM" "$OUT/fab/notes/vendor-dfm-approval.json"
  ( cd "$OUT/fab/board" && zip -qX -r ../board-upload.zip gerbers netlist.ipc ipc2581.xml drill-report.txt ../notes )
  ( cd "$OUT/fab/panel" && zip -qX -r ../panel-upload.zip gerbers netlist.ipc ipc2581.xml drill-report.txt ../notes )
  ( cd "$OUT/assembly" && zip -qX -r "$OUT/fab/assembly-variants.zip" . )
else
  find "$OUT/fab" -maxdepth 1 -name '*upload.zip' -delete
  find "$OUT/fab" -maxdepth 1 -name 'assembly-variants.zip' -delete
fi

if [ "${MEENIE_SKIP_3D:-0}" != 1 ]; then
  echo "10 local 3D proof"
  MEENIE_BRD="$BRD" KICAD_CLI="$K" "$PY" "$ROOT/3d/build.py"
  cp "$ROOT/3d/meenie.glb" "$ROOT/3d/meenie-board.html" "$ROOT/3d/top.png" \
    "$ROOT/3d/bottom.png" "$ROOT/3d/iso.png" "$OUT/3d/"
fi

echo "11 adopt into $(basename "$FAB")"
NEW="$OUT/adopt"
rm -rf "$NEW"
mkdir -p "$NEW"
cp -R "$OUT/fab/." "$NEW/"
cp -R "$OUT/reports" "$NEW/reports"
cp -R "$OUT/assembly" "$NEW/assembly"
[ ! -d "$OUT/3d" ] || cp -R "$OUT/3d" "$NEW/3d"

CARRIED=0
while IFS= read -r rel; do
  case "$rel" in reports/SHA256SUMS.txt|reports/artifact-inventory.json) continue ;; esac
  [ ! -e "$NEW/$rel" ] || continue
  mkdir -p "$NEW/$(dirname "$rel")"
  cp "$FAB/$rel" "$NEW/$rel"
  CARRIED=$((CARRIED + 1))
  echo "   carried $rel"
done < <(cd "$FAB" && find . -type f -print | sed 's|^\./||' | sort)
echo "   $CARRIED non-generated file(s) carried forward"

for required in meenie-panel.kicad_pcb board/gerbers panel/gerbers notes/nextpcb-stackup.csv \
                assembly reports/release.json reports/drc.json reports/erc.json \
                reports/stack-gate.txt reports/panel-correspondence.txt; do
  [ -e "$NEW/$required" ] || die "adoption tree incomplete: $required"
done
"$PY" "$HERE/write_inventory.py" --root "$NEW" > /dev/null
[ -s "$NEW/reports/SHA256SUMS.txt" ] || die "adoption inventory missing"
[ -s "$NEW/reports/artifact-inventory.json" ] || die "adoption inventory missing"

PREV="$FAB.previous.$$"
rm -rf "$PREV"
[ ! -d "$FAB" ] || mv "$FAB" "$PREV"
if ! mv "$NEW" "$FAB"; then
  [ ! -d "$PREV" ] || mv "$PREV" "$FAB"
  die "adoption swap failed; $(basename "$FAB") left untouched"
fi
rm -rf "$PREV"
rm -rf "$OUT"

if [ "$ORDERABLE" = 1 ]; then
  echo "PASS  exact 8-layer release package adopted into $(basename "$FAB"); order checklist still governs upload"
else
  echo "PASS  dry-run evidence only adopted into $(basename "$FAB"); release.json lists blockers and no upload archive exists"
fi
