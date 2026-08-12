import sys, os, re, uuid, copy, math, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sexp

ROOT = os.path.abspath(os.environ.get(
    'MEENIE_ROOT', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
BRD = os.path.abspath(os.path.expanduser(
    os.environ.get('MEENIE_BRD', os.path.join(ROOT, 'hw', 'meenie.kicad_pcb'))))
PAN = os.path.abspath(os.path.expanduser(
    os.environ.get('MEENIE_PANEL', os.path.join(ROOT, 'fab', 'meenie-panel.kicad_pcb'))))
MECH = os.path.abspath(os.path.expanduser(
    os.environ.get('MEENIE_PANEL_MECH', os.path.join(ROOT, 'temp', 'panel2',
                                                     'panel-mech.kicad_pcb'))))


def outline(path):
    s = open(path).read()
    xs, ys, i = [], [], 0
    while True:
        j = s.find('(gr_', i)
        if j < 0:
            break
        d, k = 0, j
        while k < len(s):
            if s[k] == '(':
                d += 1
            elif s[k] == ')':
                d -= 1
                if d == 0:
                    break
            k += 1
        blk = s[j:k + 1]
        if 'Edge.Cuts' in blk:
            for t in ('start', 'end', 'mid', 'center'):
                m = re.search(r'\(%s (-?[\d.]+) (-?[\d.]+)\)' % t, blk)
                if m:
                    xs.append(float(m.group(1)))
                    ys.append(float(m.group(2)))
        i = k + 1
    return min(xs), min(ys), round(max(xs) - min(xs), 6), round(max(ys) - min(ys), 6)


if not os.path.isfile(BRD):
    raise SystemExit('missing source board: %s' % BRD)

BX0, BY0, BW, BH = outline(BRD)
RAIL = float(os.environ.get('MEENIE_PANEL_RAIL_MM', '5.000'))
GAP = float(os.environ.get('MEENIE_PANEL_GAP_MM', '2.000'))
MAX_W = float(os.environ.get('MEENIE_PANEL_MAX_WIDTH_MM', '100.000'))
MAX_H = float(os.environ.get('MEENIE_PANEL_MAX_HEIGHT_MM', '100.000'))
TAB_W = float(os.environ.get('MEENIE_PANEL_TAB_WIDTH_MM', '5.000'))
BITE_D = float(os.environ.get('MEENIE_PANEL_BITE_DIAMETER_MM', '0.500'))
BITE_P = float(os.environ.get('MEENIE_PANEL_BITE_PITCH_MM', '0.850'))
BITE_N = int(os.environ.get('MEENIE_PANEL_BITES_PER_TAB', '5'))
BITE_OFFSET = float(os.environ.get('MEENIE_PANEL_BITE_NORTH_OFFSET_MM', '0.500'))
FID_EDGE = float(os.environ.get('MEENIE_PANEL_FID_EDGE_MM', '3.850'))

if BW <= 0 or BH <= 0 or BW < 2 * TAB_W:
    raise SystemExit('unsafe board/tab geometry: board %.6f x %.6f, tabs %.6f' %
                     (BW, BH, TAB_W))
DERIVED_COLS = int(math.floor((MAX_W - 2 * RAIL - GAP) / (BW + GAP) + 1e-9))
DERIVED_ROWS = int(math.floor((MAX_H - 2 * RAIL - GAP) / (BH + GAP) + 1e-9))
COLS = int(os.environ.get('MEENIE_PANEL_COLS', str(DERIVED_COLS)))
ROWS = int(os.environ.get('MEENIE_PANEL_ROWS', str(DERIVED_ROWS)))
if min(COLS, ROWS, BITE_N) < 1:
    raise SystemExit('panel counts must be positive: columns %d, rows %d, bites %d' %
                     (COLS, ROWS, BITE_N))
# Keep two 5 mm edge tabs deliberately.  Both endpoints are derived from the
# selected board outline so a grown board cannot inherit stale tab coordinates.
TABS = [(0.000, TAB_W), (BW - TAB_W, BW)]

W = RAIL * 2 + COLS * BW + (COLS + 1) * GAP
H = RAIL * 2 + ROWS * BH + (ROWS + 1) * GAP
if W > MAX_W + 1e-6:
    raise SystemExit('panel width %.6f exceeds %.6f mm limit (board %.6f, columns %d)' %
                     (W, MAX_W, BW, COLS))
if H > MAX_H + 1e-6:
    raise SystemExit('panel height %.6f exceeds %.6f mm limit (board %.6f, rows %d)' %
                     (H, MAX_H, BH, ROWS))
LEFT = [RAIL + GAP + k * (BW + GAP) for k in range(COLS)]
BOTTOM = [RAIL + GAP + r * (BH + GAP) for r in range(ROWS)]
BOARDS = COLS * ROWS
TOOLING_NPTH = 4
PANEL_NPTH = BOARDS * len(TABS) * BITE_N + TOOLING_NPTH

OX, OY = 100.000, 200.000
SKIP_LAYERS = {'Edge.Cuts', 'Margin'}
XY_TAGS = {'at', 'start', 'end', 'mid', 'center', 'xy'}
SOURCE_SHA256 = hashlib.sha256(open(BRD, 'rb').read()).hexdigest()
UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    'https://meenie.invalid/panel/%s/%.6f/%.6f/%d/%d' %
    (SOURCE_SHA256, W, H, COLS, ROWS),
)
_MECH_UUID_INDEX = 0


def placements():
    out = []
    for row, y0 in enumerate(BOTTOM):
        for k, lx in enumerate(LEFT):
            out.append(dict(row=row, col=k, x0=lx, y0=y0,
                            y1=y0 + BH, rot=0, north=y0 + BH))
    return out


def K(px, py):
    return (round(OX + px, 4), round(OY - py, 4))


def U():
    global _MECH_UUID_INDEX
    _MECH_UUID_INDEX += 1
    return str(uuid.uuid5(UUID_NAMESPACE, 'mechanical:%d' % _MECH_UUID_INDEX))


def xform(p):
    dx = (OX + p['x0']) - BX0
    dy = (OY - p['y1']) - BY0
    if p['rot'] == 0:
        return (lambda x, y: (x + dx, y + dy)), 0.0
    cx = OX + p['x0'] + BW / 2.0
    cy = OY - (p['y0'] + p['y1']) / 2.0
    return (lambda x, y: (2 * cx - (x + dx), 2 * cy - (y + dy))), 180.0


def fmt(v):
    v = round(v + 0.0, 6)
    return str(int(v)) if v == int(v) else ('%.6f' % v).rstrip('0').rstrip('.')


def walk(node, f):
    if not isinstance(node, list):
        return
    t = sexp.tag(node)
    if t in XY_TAGS and len(node) >= 3:
        try:
            x, y = float(node[1]), float(node[2])
        except ValueError:
            return
        nx, ny = f[0](x, y)
        node[1], node[2] = fmt(nx), fmt(ny)
        if t == 'at':
            if len(node) >= 4 and f[1]:
                node[3] = fmt((float(node[3]) + f[1]) % 360.0)
            return
    for c in node:
        if isinstance(c, list):
            walk(c, f)


def reuuid(node, placement_index):
    if not isinstance(node, list):
        return
    if sexp.tag(node) == 'uuid' and len(node) == 2:
        original = sexp.unq(node[1])
        node[1] = '"%s"' % uuid.uuid5(
            UUID_NAMESPACE, 'placement:%d:%s' % (placement_index, original))
        return
    for c in node:
        if isinstance(c, list):
            reuuid(c, placement_index)


def instance(item, p, idx):
    n = copy.deepcopy(item)
    f = xform(p)
    if sexp.tag(n) == 'footprint':
        at = sexp.kid(n, 'at')
        nx, ny = f[0](float(at[1]), float(at[2]))
        at[1], at[2] = fmt(nx), fmt(ny)
        ang = ((float(at[3]) if len(at) >= 4 else 0.0) + f[1]) % 360.0
        if len(at) >= 4:
            at[3] = fmt(ang)
        elif ang:
            at.append(fmt(ang))
        for pr in sexp.kids(n, 'property'):
            if sexp.unq(pr[1]) == 'Reference':
                pr[2] = '"%s_%02d"' % (sexp.unq(pr[2]), idx)
    else:
        walk(n, f)
    reuuid(n, idx)
    return n


def slots():
    out = []
    # The south edge of the first row has no tabs.  Every other horizontal
    # routed lane leaves two 5 mm bridges from each board's north edge to the
    # next board (or the top process rail).  Vertical lanes remain fully routed.
    out.append((RAIL, RAIL, W - RAIL, BOTTOM[0]))
    for y0 in BOTTOM:
        y1 = y0 + BH
        for lx in LEFT:
            out.append((lx + TABS[0][1], y1, lx + TABS[1][0], y1 + GAP))
    xgaps = [(RAIL, LEFT[0])]
    xgaps.extend((LEFT[i] + BW, LEFT[i + 1]) for i in range(COLS - 1))
    xgaps.append((LEFT[-1] + BW, W - RAIL))
    for a, b in xgaps:
        out.append((a, RAIL, b, H - RAIL))
    return out


def mech():
    import pcbnew
    lines, fps, texts = [], [], []

    def seg(p0, p1, layer, width=0.100):
        a, b = K(*p0), K(*p1)
        lines.append('\t(gr_line\n\t\t(start %g %g)\n\t\t(end %g %g)\n\t\t(stroke (width %g) '
                     '(type solid))\n\t\t(layer "%s")\n\t\t(uuid "%s")\n\t)'
                     % (a[0], a[1], b[0], b[1], width, layer, U()))

    def rect(x0, y0, x1, y1, layer, width=0.100):
        for p0, p1 in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                       ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            seg(p0, p1, layer, width)

    def npth(px, py, d, ref):
        kx, ky = K(px, py)
        fps.append('\t(footprint "panel:NPTH_%g"\n\t\t(layer "F.Cu")\n\t\t(uuid "%s")\n'
                   '\t\t(at %g %g)\n\t\t(property "Reference" "%s" (at 0 -1.5 0) '
                   '(layer "F.SilkS") (hide yes) (uuid "%s") (effects (font (size 1 1) '
                   '(thickness 0.15))))\n\t\t(property "Value" "NPTH %gmm" (at 0 1.5 0) '
                   '(layer "F.Fab") (hide yes) (uuid "%s") (effects (font (size 1 1) '
                   '(thickness 0.15))))\n\t\t(attr exclude_from_pos_files exclude_from_bom)\n'
                   '\t\t(pad "" np_thru_hole circle (at 0 0) (size %g %g) (drill %g) '
                   '(layers "F&B.Cu" "*.Mask") (uuid "%s"))\n\t)'
                   % (d, U(), kx, ky, ref, U(), d, U(), d, d, d, U()))

    def fid(px, py, ref):
        kx, ky = K(px, py)
        fps.append('\t(footprint "panel:Fiducial_1mm"\n\t\t(layer "F.Cu")\n\t\t(uuid "%s")\n'
                   '\t\t(at %g %g)\n\t\t(property "Reference" "%s" (at 0 -1.5 0) '
                   '(layer "F.SilkS") (hide yes) (uuid "%s") (effects (font (size 1 1) '
                   '(thickness 0.15))))\n\t\t(property "Value" "fiducial 1.000/2.000" '
                   '(at 0 1.5 0) (layer "F.Fab") (hide yes) (uuid "%s") (effects (font '
                   '(size 1 1) (thickness 0.15))))\n\t\t(attr smd exclude_from_pos_files '
                   'exclude_from_bom)\n\t\t(pad "1" smd circle (at 0 0) (size 1 1) '
                   '(layers "F.Cu" "F.Mask") (solder_mask_margin 0.5) (uuid "%s"))\n'
                   '\t\t(pad "1" smd circle (at 0 0) (size 1 1) (layers "B.Cu" "B.Mask") '
                   '(solder_mask_margin 0.5) (uuid "%s"))\n\t)'
                   % (U(), kx, ky, ref, U(), U(), U(), U()))

    def txt(px, py, s, layer="Dwgs.User", size=1.0):
        kx, ky = K(px, py)
        texts.append('\t(gr_text "%s"\n\t\t(at %g %g 0)\n\t\t(layer "%s")\n\t\t(uuid "%s")\n'
                     '\t\t(effects (font (size %g %g) (thickness %g)) (justify left bottom))\n\t)'
                     % (s, kx, ky, layer, U(), size, size, size * 0.15))

    rect(0, 0, W, H, "Edge.Cuts")
    MM = pcbnew.FromMM
    ps = pcbnew.SHAPE_POLY_SET()
    for x0, y0, x1, y1 in slots():
        r = pcbnew.SHAPE_POLY_SET()
        r.NewOutline()
        for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            kx, ky = K(px, py)
            r.Append(MM(kx), MM(ky))
        ps.BooleanAdd(r)
    ps.Simplify()
    nseg = 0
    for oi in range(ps.OutlineCount()):
        o = ps.Outline(oi)
        pts = [(pcbnew.ToMM(o.CPoint(j).x) - OX, OY - pcbnew.ToMM(o.CPoint(j).y))
               for j in range(o.PointCount())]
        for j in range(len(pts)):
            seg(pts[j], pts[(j + 1) % len(pts)], "Edge.Cuts")
            nseg += 1

    for i, (px, py) in enumerate([(2.500, 2.500), (W - 2.500, 2.500),
                                  (2.500, H - 2.500), (W - 2.500, H - 2.500)]):
        npth(px, py, 3.050, "TH%d" % (i + 1))
    # Three asymmetric global fiducials prevent a 180-degree panel ambiguity.
    for i, (px, py) in enumerate([(10.000, FID_EDGE), (W - 10.000, FID_EDGE),
                                  (10.000, H - FID_EDGE)]):
        fid(px, py, "FID%d" % (i + 1))
    nb = 0
    for bd in placements():
        for t0, t1 in TABS:
            bite0 = (TAB_W - (BITE_N - 1) * BITE_P) / 2.0
            for j in range(BITE_N):
                npth(bd['x0'] + t0 + bite0 + j * BITE_P,
                     bd['north'] + BITE_OFFSET, BITE_D, "MB%d" % (nb + 1))
                nb += 1
        rect(bd['x0'], bd['y0'], bd['x0'] + BW, bd['y1'], "Dwgs.User", 0.050)
        txt(bd['x0'] + 0.3, bd['y0'] + 0.3,
            "R%dC%d %d deg" % (bd['row'] + 1, bd['col'] + 1, bd['rot']), size=0.8)
        seg((bd['x0'], bd['north']), (bd['x0'] + BW, bd['north']), "Dwgs.User", 0.150)
    txt(2.0, H - 1.6, "meenie panel  %d x %d  %d boards  %.3f x %.3f mm" %
        (COLS, ROWS, COLS * ROWS, W, H), size=1.6)
    txt(2.0, 1.2, "tabs NORTH EDGE ONLY, 2 x %.3f mm per board, mouse bites %.3f mm on "
        "%.3f mm pitch, centres %.3f mm into routed gap" %
        (TAB_W, BITE_D, BITE_P, BITE_OFFSET), size=1.0)
    txt(2.0, 3.0, "rails %.3f  routed gaps %.3f  all boards 0 deg  tooling 4 x "
        "3.050 NPTH  asymmetric fiducials 3 x 1.000 at %.3f"
        % (RAIL, GAP, FID_EDGE), size=1.0)

    brd = sexp.parse(open(BRD).read())
    head = ['(kicad_pcb']
    for k in ('version', 'generator_version'):
        head.append('\t' + sexp.dump(sexp.kid(brd, k)))
    head.append('\t(generator "meenie-panel")')
    head.append('\t' + sexp.dump(sexp.kid(brd, 'general'), 1))
    head.append('\t(paper "A3")')
    for k in ('layers', 'setup'):
        head.append('\t' + sexp.dump(sexp.kid(brd, k), 1))
    os.makedirs(os.path.dirname(MECH), exist_ok=True)
    open(MECH, 'w').write('\n'.join(head + fps + lines + texts + ['\t(embedded_fonts no)', ')']) + '\n')
    print('mech %.3f x %.3f mm  outlines %d  edge segments %d  bites %d  footprints %d'
          % (W, H, ps.OutlineCount(), nseg + 4, nb, len(fps)))


def build(dest, mech_only=False):
    brd = sexp.parse(open(BRD).read())
    pan = sexp.parse(open(MECH).read())
    src = []
    for x in brd[1:]:
        if not isinstance(x, list):
            continue
        if sexp.tag(x) in ('version', 'generator', 'generator_version', 'general',
                           'paper', 'layers', 'setup', 'embedded_fonts'):
            continue
        lay = sexp.kid(x, 'layer')
        if lay and sexp.unq(lay[1]) in SKIP_LAYERS:
            continue
        src.append(x)
    body = [x for x in pan[1:] if isinstance(x, list)
            and sexp.tag(x) not in ('version', 'generator', 'generator_version', 'general',
                                    'paper', 'layers', 'setup', 'net', 'embedded_fonts')]
    mechn = len(body)
    ps = [] if mech_only else placements()
    for i, p in enumerate(ps):
        for it in src:
            body.append(instance(it, p, i + 1))
    out = ['(kicad_pcb']
    for k in ('version', 'generator_version'):
        out.append('\t' + sexp.dump(sexp.kid(brd, k)))
    out.append('\t(generator "meenie-panel")')
    out.append('\t' + sexp.dump(sexp.kid(brd, 'general'), 1))
    out.append('\t' + sexp.dump(sexp.kid(pan, 'paper'), 1))
    for k in ('layers', 'setup'):
        out.append('\t' + sexp.dump(sexp.kid(brd, k), 1))
    for n in body:
        out.append('\t' + sexp.dump(n, 1))
    out += ['\t(embedded_fonts no)', ')']
    dest = os.path.abspath(os.path.expanduser(dest))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, 'w').write('\n'.join(out) + '\n')
    print('%s  placements %d  board items %d  mech items %d  total %d'
          % (dest, len(ps), len(src), mechn, len(body)))


if __name__ == '__main__':
    a = sys.argv[1:]
    if '--mech' in a:
        mech()
    elif '--mech-only' in a:
        build(a[a.index('--mech-only') + 1], True)
    else:
        build(a[0] if a else PAN)
