import re, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel as step

U = 1000000


def placements():
    return [(p['x0'], p['y0'], p['y1'], p['rot']) for p in step.placements()]


def tf(p):
    x0, y0, y1, rot = p
    if rot == 0:
        dx = round(((step.OX + x0) - step.BX0) * U)
        dy = round(((step.OY - y1) - step.BY0) * U)
        return lambda x, y: (x + dx, y - dy)
    ax = round((2 * (step.OX + x0 + step.BW / 2.0) - ((step.OX + x0) - step.BX0)) * U)
    ay = round((2 * (step.OY - (y0 + y1) / 2.0) - ((step.OY - y1) - step.BY0)) * U)
    return lambda x, y: (ax - x, -ay - y)


ADD = re.compile(r'^%ADD(\d+)(.*?)\*%$')
CMD = re.compile(r'([A-Za-z]+)(-?\d+)?')


def parse(path):
    ap = {}
    flashes = []
    draws = []
    regions = []
    cur = None
    x = y = 0
    interp = 1
    region = False
    poly = []
    scale = None
    for raw in open(path):
        line = raw.strip()
        if not line:
            continue
        m = ADD.match(line)
        if m:
            ap[int(m.group(1))] = m.group(2)
            continue
        if line.startswith('%FSLAX'):
            scale = line
            continue
        if line.startswith('%') or line.startswith('G04'):
            continue
        body = line[:-1] if line.endswith('*') else line
        nx, ny, ni, nj = None, None, None, None
        op = None
        pos = 0
        while pos < len(body):
            mm = re.match(r'([GDXYIJMT])(-?\d+)', body[pos:])
            if not mm:
                break
            c, v = mm.group(1), int(mm.group(2))
            pos += mm.end()
            if c == 'G':
                if v in (1, 2, 3):
                    interp = v
                elif v == 36:
                    region, poly = True, []
                elif v == 37:
                    region = False
                    if poly:
                        regions.append(tuple(poly))
                    poly = []
            elif c == 'D':
                if v >= 10:
                    cur = v
                else:
                    op = v
            elif c == 'X':
                nx = v
            elif c == 'Y':
                ny = v
            elif c == 'I':
                ni = v
            elif c == 'J':
                nj = v
        px, py = x, y
        if nx is not None:
            x = nx
        if ny is not None:
            y = ny
        if op == 3:
            flashes.append((ap.get(cur), x, y))
        elif op == 1:
            if region:
                if not poly:
                    poly.append((px, py))
                poly.append((x, y))
            else:
                cx = px + (ni or 0)
                cy = py + (nj or 0)
                a, b = (px, py), (x, y)
                key = (ap.get(cur), interp, a, b, (cx, cy) if interp != 1 else None)
                draws.append(key)
        elif op == 2 and region:
            if poly:
                regions.append(tuple(poly))
            poly = [(x, y)]
    return dict(scale=scale, ap=ap, flashes=flashes, draws=draws, regions=regions)


def canon_poly(pts, f=None):
    q = [f(*p) for p in pts] if f else list(pts)
    d = [p for i, p in enumerate(q) if i == 0 or p != q[i - 1]]
    while len(d) > 1 and d[0] == d[-1]:
        d.pop()
    if not d:
        return ()
    cands = []
    for seq in (d, d[::-1]):
        for i in range(len(seq)):
            cands.append(tuple(seq[i:] + seq[:i]))
    return min(cands)


def canon_draw(d, f=None):
    a, i, p0, p1, c = d
    if f:
        p0, p1 = f(*p0), f(*p1)
        c = f(*c) if c else None
    lo, hi = (p0, p1) if p0 <= p1 else (p1, p0)
    return (a, i, lo, hi, c)


def compare(bpath, ppath, extra_flash=0):
    B, P = parse(bpath), parse(ppath)
    ps = placements()
    ef = collections.Counter()
    ed = collections.Counter()
    er = collections.Counter()
    for p in ps:
        f = tf(p)
        for a, x, y in B['flashes']:
            ef[(a,) + f(x, y)] += 1
        for d in B['draws']:
            ed[canon_draw(d, f)] += 1
        for r in B['regions']:
            er[canon_poly(r, f)] += 1
    af = collections.Counter((a, x, y) for a, x, y in P['flashes'])
    ad = collections.Counter(canon_draw(d) for d in P['draws'])
    ar = collections.Counter(canon_poly(r) for r in P['regions'])
    res = {}
    for name, exp, act in (('flash', ef, af), ('draw', ed, ad), ('region', er, ar)):
        miss = exp - act
        extra = act - exp
        res[name] = (sum(exp.values()), sum(act.values()), sum(miss.values()), sum(extra.values()),
                     list(miss.items())[:3], list(extra.items())[:3])
    return B, P, res


if __name__ == '__main__':
    import os
    bd, pd = sys.argv[1], sys.argv[2]
    pairs = []
    for fn in sorted(os.listdir(bd)):
        if fn.endswith('.gbrjob'):
            continue
        suf = fn[len('meenie-'):]
        pf = os.path.join(pd, 'meenie-panel-' + suf)
        if os.path.exists(pf):
            pairs.append((fn, os.path.join(bd, fn), pf))
    tot = collections.Counter()
    for fn, b, p in pairs:
        if 'Edge_Cuts' in fn:
            continue
        B, P, r = compare(b, p)
        line = []
        ok = True
        for k in ('flash', 'draw', 'region'):
            e, a, m, x, ms, xs = r[k]
            line.append('%s exp %d act %d miss %d extra %d' % (k, e, a, m, x))
            tot[k + '_exp'] += e
            tot[k + '_act'] += a
            tot[k + '_miss'] += m
            tot[k + '_extra'] += x
            if m or x:
                ok = False
        print(('OK  ' if ok else 'FAIL') + ' ' + fn.ljust(26) + ' | '.join(line))
        if not ok:
            for k in ('flash', 'draw', 'region'):
                e, a, m, x, ms, xs = r[k]
                if ms:
                    print('      missing %s sample %s' % (k, ms[:2]))
                if xs:
                    print('      extra   %s sample %s' % (k, xs[:2]))
    print(dict(tot))
