#!/usr/bin/env python3
"""Create unambiguous NextPCB BOM/centroid packages for all RF populations.

Input files are deliberately raw KiCad exports.  Coordinates and rotations are
not reinterpreted: NextPCB documents positive counter-clockwise rotation and
accepts KiCad centroid files, so preserving KiCad's board/Gerber origin is the
least ambiguous convention.  The script only normalizes headers and filters
the DNP population.
"""
import argparse, csv, pathlib, re, zipfile
from collections import defaultdict
from xml.sax.saxutils import escape

PARTS = {
    "C161404": ("Murata", "GRM0335C1H120GA01D"),
    "C526929": ("Yageo", "CC0201FRNPO9BN120"),
    "C85923": ("Murata Electronics", "GRM033C71C104KE14D"),
    "C272870": ("Yageo", "CC0201JRNPO9BN101"),
    "C69335": ("Samsung Electro-Mechanics", "CL10A475KA8NQNC"),
    "C52923": ("Samsung Electro-Mechanics", "CL05A105KA5NQNC"),
    "C408126": ("Murata Electronics", "GJM0335C1ER80WB01D"),
    "C237424": ("Murata Electronics", "GJM0335C1HR50WB01D"),
    "C185597": ("Murata Electronics", "GRM033R71E821KA01D"),
    "C239819": ("Will Semiconductor", "ESD5311Z-2/TR"),
    "C85402": ("Nexperia", "PESD5V0S1UL,315"),
    "C364556": ("Lite-On", "LTST-C281KRKT"),
    "C5187472": ("SHOU HAN", "TYPE-C 16P QTWT"),
    "C237443": ("Murata Electronics", "LQP03HQ4N7H02D"),
    "C327251": ("Yageo", "RC0402FR-072R2L"),
    "C270344": ("Uniroyal Electronics", "0201WMF5101TEE"),
    "C270365": ("Uniroyal Electronics", "0201WMF1001TEE"),
    "C190794": ("Nordic Semiconductor", "NRF52840-QIAA-R"),
    "C1986927": ("Seiko Epson", "Q22FA12800043"),
    "C255986": ("Seiko Epson", "X1E0002510059"),
    "C337939": ("Murata Electronics", "LQP03HQ3N9B02D"),
    "C1864607": ("Murata Electronics", "GJM0335C1H2R4WB01D"),
    "C3902158": ("Samsung Electro-Mechanics", "CL05A106MP6NUN8"),
    "C318563": ("Samsung Electro-Mechanics", "CL05A475MO5NUNC"),
    "C1972949": ("Texas Instruments", "TPD1E01B04DPLR"),
    "C914099": ("Texas Instruments", "TPD1E6B06DPLR"),
}

# Parts LCSC does not list under the ordered code.  The schematic carries the
# reviewed manufacturer and MPN, so the BOM line only needs a distributor
# number, and the assembler quotes it as a customer-supplied or alternate line.
ALT_SOURCE = {
    ("C&K/Littelfuse", "GSNANOT000102"): "DigiKey 108-NANOT160ASCT-ND",
}

# These parts were introduced after the legacy BOM map.  They are allowed in
# the normalized assembly outputs only when the source schematic itself carries
# the reviewed identity; a curated fallback must never conceal stale source
# metadata on the frozen release candidate.
SOURCE_METADATA_REQUIRED = {"C161404", "C255986", "C1972949", "C914099"}

VARIANTS = {
    "A-4n7-0p5": {"L2": ("4.7nH", "C237443"), "C16": ("0.5pF", "C237424")},
    "B-4n7-0p8": {"L2": ("4.7nH", "C237443"), "C16": ("0.8pF", "C408126")},
    "C-3n9-DNP": {"L2": ("3.9nH", "C337939"), "C16": None},
    "D-3n9-2p4": {"L2": ("3.9nH", "C337939"), "C16": ("2.4pF", "C1864607")},
}


def colname(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, headers, rows):
    """Minimal standards-compliant one-sheet XLSX, using only stdlib."""
    def cell(r, c, value):
        ref = "%s%d" % (colname(c), r)
        if isinstance(value, (int, float)):
            return '<c r="%s"><v>%s</v></c>' % (ref, value)
        return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(str(value)))
    allrows = [headers] + rows
    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetData>']
    for ri, row in enumerate(allrows, 1):
        sheet.append('<row r="%d">%s</row>' %
                     (ri, ''.join(cell(ri, ci, v) for ci, v in enumerate(row, 1))))
    sheet += ['</sheetData></worksheet>']
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '</Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?>'
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
          '</Relationships>',
        'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="NextPCB BOM" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8"?>'
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
          '</Relationships>',
        'xl/worksheets/sheet1.xml': ''.join(sheet),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def read_bom(path):
    rows = list(csv.DictReader(open(path, newline='')))
    byref = {}
    for r in rows:
        ref = r['Designator']
        code = r['LCSC Part'].strip()
        source_mfr = r.get('Manufacturer', '').strip()
        source_mpn = r.get('MPN', '').strip()
        if not code or code == 'NOT SELECTED':
            match ALT_SOURCE.get((source_mfr, source_mpn)):
                case None:
                    raise SystemExit('fitted %s has no distributor part number' % ref)
                case distributor:
                    byref[ref] = {**r, 'LCSC Part': distributor}
            continue
        if code not in PARTS:
            raise SystemExit('no verified MPN mapping for %s on %s' % (code, ref))
        mfr, mpn = PARTS[code]
        if code in SOURCE_METADATA_REQUIRED and (not source_mfr or not source_mpn):
            raise SystemExit('source metadata missing for %s (%s): Manufacturer/MPN required' %
                             (ref, code))
        if source_mfr and source_mfr.casefold() != mfr.casefold():
            raise SystemExit('manufacturer mapping mismatch for %s: %s != %s' %
                             (ref, source_mfr, mfr))
        # Schematic MPN fields, when present, are a positive cross-check.
        if source_mpn and source_mpn.upper() != mpn.upper():
            raise SystemExit('MPN mapping mismatch for %s: %s != %s' % (ref, source_mpn, mpn))
        r.update(Manufacturer=mfr, MPN=mpn)
        byref[ref] = r
    return byref


def apply_variant(base, overrides):
    rows = {k: dict(v) for k, v in base.items()}
    for ref, spec in overrides.items():
        if spec is None:
            rows.pop(ref, None)
            continue
        value, code = spec
        mfr, mpn = PARTS[code]
        rows[ref].update(Value=value, **{'LCSC Part': code, 'Manufacturer': mfr, 'MPN': mpn})
    return rows


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
        out.append([designators, len(refs) * panel_count, mfr, mpn, value, fp, lcsc,
                    'RF population per variant name; no substitutions without approval'])
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
    ap.add_argument('--unit-pos', required=True)
    ap.add_argument('--panel-pos', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--panel-count', type=int, required=True)
    args = ap.parse_args()
    base = read_bom(args.bom)
    unit_pos, panel_pos = read_pos(args.unit_pos), read_pos(args.panel_pos)
    out = pathlib.Path(args.out)
    bh = ['Designator', 'Quantity', 'Manufacturer', 'MPN', 'Value', 'Footprint',
          'Distributor Part Number', 'Notes']
    ch = ['Designator', 'Mid X (mm)', 'Mid Y (mm)', 'Layer', 'Rotation (deg CCW)',
          'Value', 'Package']
    summary = []
    for name, changes in VARIANTS.items():
        parts = apply_variant(base, changes)
        dst = out / name
        ub = grouped_bom(parts)
        pb = grouped_bom(parts, args.panel_count)
        uc = centroid(unit_pos, parts)
        panel_refs = {'%s_%02d' % (ref, i) for i in range(1, args.panel_count + 1)
                      for ref in parts}
        pc = centroid(panel_pos, panel_refs)
        for stem, rows in (('unit-bom', ub), ('panel-bom', pb)):
            write_csv(dst / (stem + '.csv'), bh, rows)
            write_xlsx(dst / (stem + '.xlsx'), bh, rows)
        write_csv(dst / 'unit-centroid.csv', ch, uc)
        write_csv(dst / 'panel-centroid.csv', ch, pc)
        write_xlsx(dst / 'unit-centroid.xlsx', ch, uc)
        write_xlsx(dst / 'panel-centroid.xlsx', ch, pc)
        summary.append((name, len(parts), len(ub), len(uc), len(pc)))
    print('variant,fitted_designators,bom_lines,unit_positions,panel_positions')
    for row in summary:
        print(','.join(map(str, row)))


if __name__ == '__main__':
    main()
