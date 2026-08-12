#!/usr/bin/env python3
import json, os, pathlib, re, struct, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
BRD = pathlib.Path(os.environ.get("MEENIE_BRD", HERE.parent / "hw/meenie.kicad_pcb")).resolve()
CLI = os.environ.get("KICAD_CLI", "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
OUT = pathlib.Path(os.environ.get("MEENIE_3D_OUT", HERE)).resolve()
OUT.mkdir(parents=True, exist_ok=True)

GLB = OUT / "meenie.glb"
GLB_TEMP = OUT / "$tempfile$.bin.tmp"
VIEWS = {"top.png": ["--side", "top"],
         "bottom.png": ["--side", "bottom"],
         "iso.png": ["--rotate", "-45,0,45"]}


def kc(*args):
    result = subprocess.run([CLI, *args], capture_output=True, text=True)
    output = "\n".join(line for line in (result.stdout + result.stderr).splitlines()
                       if line and not line.startswith("Fontconfig") and
                       not line.startswith("Rendering:"))
    if result.returncode:
        sys.exit("%s\n%s" % (" ".join(args[:3]), output))
    return output


def outline(source):
    points, cursor = [], 0
    while (start := source.find("(gr_", cursor)) >= 0:
        depth, end = 0, start
        while True:
            depth += (source[end] == "(") - (source[end] == ")")
            if not depth:
                break
            end += 1
        if "Edge.Cuts" in source[start:end + 1]:
            points += re.findall(
                r"\((?:start|end|mid|center) (-?[\d.]+) (-?[\d.]+)\)",
                source[start:end + 1],
            )
        cursor = end + 1
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def footprints(source):
    cursor = 0
    while (start := source.find("(footprint ", cursor)) >= 0:
        depth, end = 0, start
        while True:
            depth += (source[end] == "(") - (source[end] == ")")
            if not depth:
                break
            end += 1
        yield source[start:end + 1]
        cursor = end + 1


source = BRD.read_text()
missing = sorted({model for model in re.findall(r'\(model "([^"]+)"', source)
                  if not (BRD.parent / model.replace("${KIPRJMOD}/", "")).exists()})
if missing:
    sys.exit(json.dumps({"missing_models": missing}))
fitted = {
    re.search(r'\(property "Reference" "([^"]+)"', footprint).group(1): footprint
    for footprint in footprints(source)
    if "TestPad" not in footprint.split(None, 2)[1]
}

GLB_TEMP.unlink(missing_ok=True)
kc("pcb", "export", "glb", "-o", str(GLB), "-f", "--include-tracks", "--include-pads",
   "--include-zones", "--include-silkscreen", "--no-dnp", "--min-distance", "0.02mm",
   str(BRD))

glb = GLB.read_bytes()
gltf = json.loads(glb[20:20 + struct.unpack_from("<I", glb, 12)[0]])
exported = {node["name"] for node in gltf["nodes"]
            if "name" in node and ("mesh" in node or node.get("children"))}
bodyless = sorted(reference for reference, footprint in fitted.items()
                  if reference not in exported and "(attr through_hole)" not in footprint)

for name, arguments in VIEWS.items():
    kc("pcb", "render", "-o", str(OUT / name), "-w", "1600", "-h", "1200",
       "--quality", "high", "--background", "opaque", *arguments, str(BRD))

width, height = outline(source)
print(json.dumps({
    "board_mm": [round(width, 3), round(height, 3)],
    "bodyless_references": bodyless,
    "outputs": {path.name: path.stat().st_size for path in [GLB] + [OUT / name for name in VIEWS]},
}, sort_keys=True))
