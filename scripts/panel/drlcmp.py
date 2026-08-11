import re, sys, collections
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerbcmp as G

U = 1000000
TOOL = re.compile(r'^T(\d+)C([\d.]+)')
XY = re.compile(r'X(-?[\d.]+)Y(-?[\d.]+)')


def parse(path):
    if not path or not os.path.exists(path):
        return {}, []
    tools = {}
    cur = None
    holes = []
    for line in open(path):
        line = line.strip()
        if line.startswith(';') or not line:
            continue
        m = TOOL.match(line)
        if m and 'C' in line:
            tools[int(m.group(1))] = float(m.group(2))
            continue
        m = re.match(r'^T(\d+)$', line)
        if m:
            cur = int(m.group(1))
            continue
        pts = XY.findall(line)
        if pts:
            d = tools.get(cur)
            q = [(round(float(a) * U), round(float(b) * U)) for a, b in pts]
            holes.append((d, tuple(q), 'G85' in line))
    return tools, holes


def key(h, f=None):
    d, pts, slot = h
    q = [f(x, y) for x, y in pts] if f else list(pts)
    return (d, tuple(sorted(q)), slot)


def run(b, m, p):
    _, B = parse(b)
    _, M = parse(m)
    _, P = parse(p)
    exp = collections.Counter()
    for pl in G.placements():
        f = G.tf(pl)
        for h in B:
            exp[key(h, f)] += 1
    for h in M:
        exp[key(h)] += 1
    act = collections.Counter(key(h) for h in P)
    miss, extra = exp - act, act - exp
    # Excellon coordinates are emitted on a 0.001 mm grid.  A feature exactly
    # between grid points can round in the opposite direction after a 180-degree
    # panel transform.  Cancel only identical tools/slot shapes within one grid
    # quantum; every larger displacement remains a hard mismatch.
    tolerated = 0
    ex = list(extra.items())
    for mk, mc in list(miss.items()):
        md, mpts, mslot = mk
        left = mc
        for i, (xk, xc) in enumerate(ex):
            xd, xpts, xslot = xk
            close = (md == xd and mslot == xslot and len(mpts) == len(xpts) and
                     all(abs(a - b) <= 1000 and abs(c - d) <= 1000
                         for (a, c), (b, d) in zip(mpts, xpts)))
            if left and xc and close:
                n = min(left, xc)
                left -= n
                ex[i] = (xk, xc - n)
                tolerated += n
        if left:
            miss[mk] = left
        else:
            del miss[mk]
    extra = collections.Counter({k: c for k, c in ex if c})
    filename = os.path.basename(p)
    label = filename.split('-panel-', 1)[1][:-4] if '-panel-' in filename else filename[:-4]
    print('%-10s board %3d  mech %3d  expected %4d  actual %4d  miss %d  extra %d'
          % (label, len(B), len(M), sum(exp.values()), sum(act.values()),
             sum(miss.values()), sum(extra.values())))
    if tolerated:
        print('    tolerated %d coordinate roundings <= 0.001 mm' % tolerated)
    for n, c in (('miss', miss), ('extra', extra)):
        if c:
            print('   ', n, list(c.items())[:3])
    return sum(miss.values()) + sum(extra.values())


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: drlcmp.py BOARD.drl MECH.drl-or-- PANEL.drl')
    print('drill mismatches', run(sys.argv[1], None if sys.argv[2] == '-' else sys.argv[2],
                                  sys.argv[3]))
