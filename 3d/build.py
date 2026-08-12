#!/usr/bin/env python3
import base64, json, os, pathlib, re, struct, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
BRD = pathlib.Path(os.environ.get("MEENIE_BRD", HERE.parent / "hw/meenie.kicad_pcb")).resolve()
CLI = os.environ.get("KICAD_CLI", "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
OUT = pathlib.Path(os.environ.get("MEENIE_3D_OUT", HERE)).resolve()
OUT.mkdir(parents=True, exist_ok=True)

GLB = OUT / "meenie.glb"
GLB_TEMP = OUT / "$tempfile$.bin.tmp"
HTML = OUT / "meenie.html"
VIEWS = {"top.png": ["--side", "top"],
         "bottom.png": ["--side", "bottom"],
         "iso.png": ["--rotate", "-45,0,45"]}

VIEWER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meenie</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
<style>
*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;background:#080a0d;color:#f3f5f7;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}main,model-viewer{width:100%;height:100%}model-viewer{background:radial-gradient(circle at 50% 42%,#242a31 0,#101318 48%,#080a0d 100%)}nav{position:fixed;z-index:2;left:50%;bottom:24px;display:flex;gap:6px;transform:translateX(-50%);padding:5px;border:1px solid #30363d;border-radius:9px;background:#11161dcc;backdrop-filter:blur(12px)}button{border:0;border-radius:5px;padding:8px 12px;background:transparent;color:#c9d1d9;font:inherit;cursor:pointer}button:hover,button:focus-visible{background:#28313b;color:#fff;outline:0}h1{position:fixed;z-index:2;top:20px;left:24px;margin:0;font-size:13px;letter-spacing:.24em;font-weight:600}</style>
</head>
<body>
<main>
<h1>MEENIE</h1>
<model-viewer id="board" src="data:model/gltf-binary;base64,__MODEL__" alt="Meenie controller board" camera-controls auto-rotate rotation-per-second="12deg" shadow-intensity="1" shadow-softness=".8" exposure="1.05" field-of-view="35deg" camera-orbit="45deg 58deg 115%" loading="eager" poster="data:image/png;base64,__POSTER__"></model-viewer>
<nav aria-label="Camera view">
<button data-orbit="0deg 0deg">TOP</button>
<button data-orbit="45deg 58deg">ISO</button>
<button data-orbit="0deg 180deg">BOTTOM</button>
<button id="spin">PAUSE</button>
</nav>
</main>
<script>
const board=document.querySelector('#board');
const radius=innerWidth<600?'0.05m':'115%';
board.setAttribute('camera-orbit',`45deg 58deg ${radius}`);
document.querySelectorAll('[data-orbit]').forEach(button=>button.onclick=()=>board.setAttribute('camera-orbit',`${button.dataset.orbit} ${radius}`));
document.querySelector('#spin').onclick=event=>{board.autoRotate=!board.autoRotate;event.currentTarget.textContent=board.autoRotate?'PAUSE':'SPIN'};
</script>
</body>
</html>
"""


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

HTML.write_text(
    VIEWER.replace("__MODEL__", base64.b64encode(GLB.read_bytes()).decode())
          .replace("__POSTER__", base64.b64encode((OUT / "iso.png").read_bytes()).decode())
)

width, height = outline(source)
print(json.dumps({
    "board_mm": [round(width, 3), round(height, 3)],
    "bodyless_references": bodyless,
    "outputs": {path.name: path.stat().st_size
                for path in [GLB, HTML] + [OUT / name for name in VIEWS]},
}, sort_keys=True))
