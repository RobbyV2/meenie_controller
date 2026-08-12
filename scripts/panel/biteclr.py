import sys, os, math, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerbcmp as G, drlcmp as D

U = 1e6
PD = os.environ.get('MEENIE_PGERB', 'fab/panel/gerbers')
PPRE = os.environ.get('MEENIE_PANEL_PREFIX', 'meenie-panel')
LAYERS = (('F.Cu', PPRE + '-F_Cu.gtl'), ('In1.Cu', PPRE + '-GND.g1'),
          ('In2.Cu', PPRE + '-GND2.g2'), ('In3.Cu', PPRE + '-HDI_PWR.g3'),
          ('In4.Cu', PPRE + '-CORE_ROUTE_L5.g4'),
          ('In5.Cu', PPRE + '-GPIO_ROUTE_L6.g5'),
          ('In6.Cu', PPRE + '-RF_GND_L7.g6'), ('B.Cu', PPRE + '-B_Cu.gbl'))
COPPER_GAP = float(os.environ.get('MEENIE_PANEL_BITE_COPPER_GAP_MM', '0.350'))


def rect_dist(dx, dy, hw, hh):
    ox, oy = max(abs(dx) - hw, 0.0), max(abs(dy) - hh, 0.0)
    return math.hypot(ox, oy)


def ap_dist(a, dx, dy):
    n = [float(v) for v in re.findall(r'-?[\d.]+', a)]
    if a.startswith('C,'):
        return math.hypot(dx, dy) - n[0] / 2
    if a.startswith('R,'):
        return rect_dist(dx, dy, n[0] / 2, n[1] / 2)
    if a.startswith('O,'):
        hw, hh = n[0] / 2, n[1] / 2
        r = min(hw, hh)
        return rect_dist(dx, dy, hw - r, hh - r) - r
    if a.startswith('RoundRect'):
        r = n[0]
        xs, ys = n[1:9:2], n[2:9:2]
        return rect_dist(dx - (max(xs) + min(xs)) / 2, dy - (max(ys) + min(ys)) / 2,
                         (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2) - r
    return math.hypot(dx, dy) - max(n) / 2


def seg_dist(px, py, a, b):
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def main(rad=0.25, box=2.0):
    _, holes = D.parse(os.path.join(PD, PPRE + '-NPTH.drl'))
    bites = [(p[0][0] / U, p[0][1] / U) for d, p, s in holes
             if abs(d - G.step.BITE_D) < 1e-9]
    print('mouse-bite holes %d' % len(bites))
    if not bites:
        raise SystemExit('no %.3f mm mouse-bite tools found' % G.step.BITE_D)
    worst = 9e9
    for lay, fn in LAYERS:
        g = G.parse(os.path.join(PD, fn))
        fl = [(a, x / U, y / U) for a, x, y in g['flashes']]
        segs = []
        for r in g['regions']:
            q = [(x / U, y / U) for x, y in r]
            segs += [(q[i], q[(i + 1) % len(q)], 0.0, 'pour') for i in range(len(q))]
        for a, i, p0, p1, c in g['draws']:
            hw = float(re.findall(r'-?[\d.]+', a)[0]) / 2 if a else 0.0
            segs.append(((p0[0] / U, p0[1] / U), (p1[0] / U, p1[1] / U), hw, 'track ' + a))
        m, at = 9e9, None
        for bx, by in bites:
            for a, x, y in fl:
                if abs(x - bx) > box or abs(y - by) > box:
                    continue
                d = ap_dist(a, bx - x, by - y)
                if d < m:
                    m, at = d, (bx, by, 'flash ' + a)
            for p0, p1, hw, kind in segs:
                if min(abs(p0[0] - bx), abs(p1[0] - bx)) > box:
                    continue
                if min(abs(p0[1] - by), abs(p1[1] - by)) > box:
                    continue
                d = seg_dist(bx, by, p0, p1) - hw
                if d < m:
                    m, at = d, (bx, by, kind)
        print('%-7s min bite-edge to copper %.4f mm   at %s' % (lay, m - rad, at))
        worst = min(worst, m - rad)
    print('worst %.4f mm  against the panel mouse-bite copper gap %.4f' %
          (worst, COPPER_GAP))
    return worst


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 0.25)
