#!/usr/bin/env python3
import base64, json, os, pathlib, re, struct, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
BRD = pathlib.Path(os.environ.get("MEENIE_BRD", HERE.parent / "hw/meenie.kicad_pcb")).resolve()
CLI = os.environ.get("KICAD_CLI", "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

GLB = HERE / "meenie.glb"
PAGE = HERE / "meenie-board.html"
VIEWS = {"top.png": ["--side", "top"],
         "bottom.png": ["--side", "bottom"],
         "iso.png": ["--rotate", "-45,0,45"]}


def kc(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    out = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                    if l and not l.startswith("Fontconfig") and not l.startswith("Rendering:"))
    if r.returncode:
        sys.exit("%s\n%s" % (" ".join(args[:3]), out))
    return out


def outline(s):
    pts, i = [], 0
    while (j := s.find("(gr_", i)) >= 0:
        d, k = 0, j
        while True:
            d += (s[k] == "(") - (s[k] == ")")
            if not d:
                break
            k += 1
        if "Edge.Cuts" in s[j:k + 1]:
            pts += re.findall(r"\((?:start|end|mid|center) (-?[\d.]+) (-?[\d.]+)\)", s[j:k + 1])
        i = k + 1
    xs = [float(x) for x, _ in pts]
    ys = [float(y) for _, y in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def page(width, height):
    html = (HERE / "page.tpl.html").read_text()
    subs = [("__GLB_B64__", base64.b64encode(GLB.read_bytes()).decode()),
            ("/*THREE*/", (HERE / "vendor/three.min.js").read_text()),
            ("/*ORBIT*/", (HERE / "vendor/OrbitControls.js").read_text()),
            ("/*GLTF*/", (HERE / "vendor/GLTFLoader.js").read_text()),
            ("/*ROOM*/", (HERE / "vendor/RoomEnvironment.js").read_text()),
            ("__BOARD_WIDTH__", "%.3f" % width),
            ("__BOARD_HEIGHT__", "%.3f" % height)]
    for k, v in subs:
        assert html.count(k) >= 1, k
        assert "</script" not in v.lower(), k
        html = html.replace(k, v)
    for pat in (r"<link\b", r"@import\b", r"""\ssrc\s*=\s*['"](?!data:)""",
                r"""\shref\s*=\s*['"](?!data:|#)""", r"""(?<![A-Za-z])url\s*\(\s*['"]?(?!data:)"""):
        assert re.search(pat, html, re.I) is None, pat
    assert not re.search(r"__[A-Z0-9_]+__", html.split('<script id="glb"', 1)[0]), "unexpanded token"
    PAGE.write_text(html)


def footprints(s):
    i = 0
    while (j := s.find("(footprint ", i)) >= 0:
        d, k = 0, j
        while True:
            d += (s[k] == "(") - (s[k] == ")")
            if not d:
                break
            k += 1
        yield s[j:k + 1]
        i = k + 1


src = BRD.read_text()
missing = sorted({m for m in re.findall(r'\(model "([^"]+)"', src)
                  if not (BRD.parent / m.replace("${KIPRJMOD}/", "")).exists()})
if missing:
    sys.exit("unresolved 3D models:\n  " + "\n  ".join(missing))
fitted = {re.search(r'\(property "Reference" "([^"]+)"', f).group(1): f
          for f in footprints(src) if "TestPad" not in f.split(None, 2)[1]}

kc("pcb", "export", "glb", "-o", str(GLB), "-f", "--include-tracks", "--include-pads",
   "--include-zones", "--include-silkscreen", "--no-dnp", "--min-distance", "0.02mm", str(BRD))

glb = GLB.read_bytes()
gltf = json.loads(glb[20:20 + struct.unpack_from("<I", glb, 12)[0]])
exported = {n["name"] for n in gltf["nodes"] if "name" in n and ("mesh" in n or n.get("children"))}
bodyless = sorted(r + (" (DNP)" if re.search(r"\(attr[^)]*\bdnp\b", f) else "")
                  for r, f in fitted.items() if r not in exported)

for name, args in VIEWS.items():
    kc("pcb", "render", "-o", str(HERE / name), "-w", "1600", "-h", "1200",
       "--quality", "high", "--background", "opaque", *args, str(BRD))

w, h = outline(src)
page(w, h)

for p in [GLB, PAGE] + [HERE / n for n in VIEWS]:
    print(p.name, p.stat().st_size)
print("outline %.3f x %.3f mm" % (w, h))
print("no body in glb, renders as bare pads:", ", ".join(bodyless) or "none")
