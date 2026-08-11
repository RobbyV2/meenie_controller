import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gerbcmp as G, drlcmp as D
from product import COPPER, drill_specs, load_manifest

BD = os.environ.get('MEENIE_BGERB', 'temp/panel985/bgerb')
MD = os.environ.get('MEENIE_MGERB', 'temp/panel985/mgerb')
PD = sys.argv[1] if len(sys.argv) > 1 else 'fab/panel/gerbers'
BPRE = os.environ.get('MEENIE_BOARD_PREFIX', 'meenie')
MPRE = os.environ.get('MEENIE_MECH_PREFIX', 'mech')
PPRE = os.environ.get('MEENIE_PANEL_PREFIX', 'meenie-panel')
MANIFEST = os.environ.get('MEENIE_STACK_MANIFEST')


def near_point(a, b, tol):
    return (len(a) == len(b) == 2 and abs(a[0] - b[0]) <= tol and
            abs(a[1] - b[1]) <= tol)


def near_feature(kind, a, b, tol):
    if kind == 'flash':
        return a[0] == b[0] and near_point(a[1:], b[1:], tol)
    if kind == 'draw':
        return (a[0:2] == b[0:2] and near_point(a[2], b[2], tol) and
                near_point(a[3], b[3], tol) and
                ((a[4] is None and b[4] is None) or
                 (a[4] is not None and b[4] is not None and near_point(a[4], b[4], tol))))
    if kind == 'region':
        return len(a) == len(b) and all(near_point(x, y, tol) for x, y in zip(a, b))
    return False


def reconcile(kind, expected, actual, tol):
    """Cancel exact items, then exporter-rounding-only coordinate deltas."""
    miss, extra = expected - actual, actual - expected
    tolerated = 0
    if not miss or not extra:
        return miss, extra, tolerated
    ex = list(extra.items())
    for mk, mc in list(miss.items()):
        left = mc
        for i, (xk, xc) in enumerate(ex):
            if left and xc and near_feature(kind, mk, xk, tol):
                n = min(left, xc)
                left -= n
                ex[i] = (xk, xc - n)
                tolerated += n
        if left:
            miss[mk] = left
        else:
            del miss[mk]
    extra = collections.Counter({k: c for k, c in ex if c})
    return miss, extra, tolerated

tot = collections.Counter()
bad = 0
expected_copper = {suffix for suffix, _ in COPPER}
actual_board_copper = {
    fn[len(BPRE) + 1:] for fn in os.listdir(BD)
    if fn.startswith(BPRE + '-') and fn.endswith(('.gtl', '.gbl', '.g1', '.g2', '.g3', '.g4', '.g5', '.g6'))
}
actual_panel_copper = {
    fn[len(PPRE) + 1:] for fn in os.listdir(PD)
    if fn.startswith(PPRE + '-') and fn.endswith(('.gtl', '.gbl', '.g1', '.g2', '.g3', '.g4', '.g5', '.g6'))
}
if actual_board_copper != expected_copper or actual_panel_copper != expected_copper:
    print('FAIL copper set board %s panel %s expected %s' %
          (sorted(actual_board_copper), sorted(actual_panel_copper), sorted(expected_copper)))
    bad += 1
for fn in sorted(os.listdir(BD)):
    if (not fn.startswith(BPRE + '-') or fn.endswith('.gbrjob') or
            fn.endswith('.drl') or 'Edge_Cuts' in fn):
        continue
    suf = fn[len(BPRE) + 1:]
    B = G.parse(os.path.join(BD, fn))
    M = G.parse(os.path.join(MD, MPRE + '-' + suf))
    P = G.parse(os.path.join(PD, PPRE + '-' + suf))
    ef, ed, er = collections.Counter(), collections.Counter(), collections.Counter()
    for pl in G.placements():
        f = G.tf(pl)
        for a, x, y in B['flashes']:
            ef[(a,) + f(x, y)] += 1
        for d in B['draws']:
            ed[G.canon_draw(d, f)] += 1
        for r in B['regions']:
            er[G.canon_poly(r, f)] += 1
    for a, x, y in M['flashes']:
        ef[(a, x, y)] += 1
    for d in M['draws']:
        ed[G.canon_draw(d)] += 1
    for r in M['regions']:
        er[G.canon_poly(r)] += 1
    af = collections.Counter((a, x, y) for a, x, y in P['flashes'])
    ad = collections.Counter(G.canon_draw(d) for d in P['draws'])
    ar = collections.Counter(G.canon_poly(r) for r in P['regions'])
    parts, ok = [], True
    for k, e, a in (('flash', ef, af), ('draw', ed, ad), ('region', er, ar)):
        # KiCad's two plot passes can differ by one nanometre after a 180 degree
        # transform.  That is the Gerber coordinate quantum, not missing copper.
        miss, extra, tolerated = reconcile(k, e, a, 1)
        parts.append('%s exp %d act %d miss %d extra %d'
                     % (k, sum(e.values()), sum(a.values()),
                        sum(miss.values()), sum(extra.values())))
        if tolerated:
            parts[-1] += ' tol1nm %d' % tolerated
        tot[k] += sum(a.values())
        tot[k + '_bad'] += sum(miss.values()) + sum(extra.values())
        if miss or extra:
            ok = False
            print('    ', k, 'miss', list(miss.items())[:2], 'extra', list(extra.items())[:2])
    bad += 0 if ok else 1
    print(('OK   ' if ok else 'FAIL ') + suf.ljust(18) + ' | '.join(parts)
          + '   [board %d/%d/%d  mech %d/%d/%d]'
          % (len(B['flashes']), len(B['draws']), len(B['regions']),
             len(M['flashes']), len(M['draws']), len(M['regions'])))

PE = G.parse(os.path.join(PD, PPRE + '-Edge_Cuts.gm1'))
ME = G.parse(os.path.join(MD, MPRE + '-Edge_Cuts.gm1'))
pe = collections.Counter(G.canon_draw(d) for d in PE['draws'])
me = collections.Counter(G.canon_draw(d) for d in ME['draws'])
print(('OK   ' if pe == me else 'FAIL ') + 'Edge_Cuts.gm1     '
      + '%d segments, identical to the mechanical drawing, no board outline copied' % len(PE['draws']))

drills = []
for fn in sorted(os.listdir(BD)):
    if fn.startswith(BPRE + '-') and fn.endswith('.drl'):
        drills.append(fn[len(BPRE) + 1:-4])
if MANIFEST:
    expected_drills = {suffix for suffix, _ in drill_specs(load_manifest(MANIFEST))}
    panel_drills = {
        fn[len(PPRE) + 1:-4] for fn in os.listdir(PD)
        if fn.startswith(PPRE + '-') and fn.endswith('.drl')
    }
    if set(drills) != expected_drills or panel_drills != expected_drills:
        print('FAIL drill set board %s panel %s expected %s' %
              (sorted(drills), sorted(panel_drills), sorted(expected_drills)))
        bad += 1
for t in drills:
    bp = os.path.join(BD, '%s-%s.drl' % (BPRE, t))
    mp = os.path.join(MD, '%s-%s.drl' % (MPRE, t))
    pp = os.path.join(PD, '%s-%s.drl' % (PPRE, t))
    if not os.path.exists(pp):
        print('FAIL missing panel drill %s' % pp)
        bad += 1
        continue
    bad += D.run(bp, mp if os.path.exists(mp) else None, pp)
print('TOTAL', dict(tot), 'failures', bad)
sys.exit(1 if bad else 0)
