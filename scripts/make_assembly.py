#!/usr/bin/env python3
"""Create the selected first-article panel BOM and centroid."""
import argparse, csv, json, pathlib
from collections import defaultdict

ALT_SOURCE = {
    ("C&K/Littelfuse", "GSNANOT000102"): "DigiKey 108-NANOT160ASCT-ND",
}

DISTRIBUTOR_OVERRIDE = {
    "C327251": "DigiKey 311-2.20LRCT-ND",
    "C1972949": "DigiKey 296-47363-1-ND",
}

FIRST_ARTICLE = "B-4n7-0p8"


def read_bom(path):
    rows = list(csv.DictReader(open(path, newline='')))
    byref = {}
    for r in rows:
        ref = r['Designator']
        code = r['LCSC Part'].strip()
        source_mfr = r.get('Manufacturer', '').strip()
        source_mpn = r.get('MPN', '').strip()
        if not source_mfr or not source_mpn:
            raise SystemExit('source metadata missing for %s: Manufacturer/MPN required' % ref)
        if not code or code == 'NOT SELECTED':
            distributor = ALT_SOURCE.get((source_mfr, source_mpn))
            if distributor is None:
                raise SystemExit('fitted %s has no distributor part number' % ref)
            byref[ref] = {**r, 'LCSC Part': distributor}
            continue
        r['LCSC Part'] = DISTRIBUTOR_OVERRIDE.get(code, code)
        byref[ref] = r
    return byref


def read_pos(path):
    out = {}
    for r in csv.DictReader(open(path, newline='')):
        out[r['Ref']] = r
    return out


def grouped_bom(parts, panel_count=1):
    groups = defaultdict(list)
    for ref, r in parts.items():
        key = (r['Manufacturer'], r['MPN'], r['Value'], r['Footprint'], r['LCSC Part'])
        groups[key].append(ref)
    out = []
    for key, refs in sorted(groups.items(), key=lambda x: min(x[1])):
        mfr, mpn, value, fp, lcsc = key
        if panel_count == 1:
            designators = ','.join(sorted(refs))
        else:
            designators = ','.join('%s_%02d' % (r, i) for i in range(1, panel_count + 1)
                                   for r in sorted(refs))
        out.append([designators, len(refs) * panel_count, mfr, mpn, value, fp, lcsc])
    return out


def centroid(pos, wanted):
    missing = sorted(set(wanted) - set(pos))
    if missing:
        raise SystemExit('centroid missing fitted refs: %s' % ','.join(missing))
    rows = []
    for ref in sorted(wanted):
        r = pos[ref]
        rows.append([ref, float(r['PosX']), float(r['PosY']), r['Side'].title(),
                     float(r['Rot']), r['Val'], r['Package']])
    return rows


def write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bom', required=True)
    ap.add_argument('--panel-pos', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--panel-count', type=int, required=True)
    args = ap.parse_args()
    parts = read_bom(args.bom)
    panel_pos = read_pos(args.panel_pos)
    out = pathlib.Path(args.out) / FIRST_ARTICLE
    bh = ['Designator', 'Quantity', 'Manufacturer', 'MPN', 'Value', 'Footprint',
          'Distributor Part Number']
    ch = ['Designator', 'Mid X (mm)', 'Mid Y (mm)', 'Layer', 'Rotation (deg CCW)',
          'Value', 'Package']
    panel_refs = {'%s_%02d' % (ref, i) for i in range(1, args.panel_count + 1)
                  for ref in parts}
    pb = grouped_bom(parts, args.panel_count)
    pc = centroid(panel_pos, panel_refs)
    write_csv(out / 'panel-bom.csv', bh, pb)
    write_csv(out / 'panel-centroid.csv', ch, pc)
    print(json.dumps({"population": FIRST_ARTICLE, "fitted": len(pc), "passed": True},
                     sort_keys=True))


if __name__ == '__main__':
    main()
