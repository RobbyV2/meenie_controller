#!/usr/bin/env python3
"""Create a fail-closed stock snapshot for the first-article population."""

import argparse, csv, datetime, http.client, json, math, pathlib, re, time, urllib.request

DIGIKEY = {
    "DigiKey 108-NANOT160ASCT-ND": {
        "available": 16474,
        "observed_utc": "2026-08-12T16:00:00+00:00",
        "url": "https://www.digikey.com/en/products/detail/c-k/GSNANOT000102/13182172",
    },
    "DigiKey 296-47363-1-ND": {
        "available": 30833,
        "observed_utc": "2026-08-12T16:00:00+00:00",
        "url": "https://www.digikey.com/en/products/detail/texas-instruments/TPD1E01B04DPLR/6098070",
    },
    "DigiKey 311-2.20LRCT-ND": {
        "available": 241776,
        "observed_utc": "2026-08-12T16:00:00+00:00",
        "url": "https://www.digikey.com/en/products/detail/yageo/RC0402FR-072R2L/1037147",
    },
}


def lcsc(code):
    url = f"https://www.lcsc.com/product-detail/{code}.html"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    failure = None
    for attempt in range(3):
        try:
            text = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
            break
        except (OSError, http.client.HTTPException) as exc:
            failure = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"LCSC request failed after three attempts: {url}: {failure}")
    model = re.search(r'"productModel":"([^"]+)"', text)
    stock = re.search(r'"inventoryLevel":(\d+)', text)
    if not model or not stock:
        raise RuntimeError(f"LCSC page lacks structured model/stock: {url}")
    return int(stock.group(1)), model.group(1), url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assembly", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()
    maximum = {}
    for row in csv.DictReader(open(args.assembly / "B-4n7-0p8" / "panel-bom.csv", newline="")):
        key = (row["Manufacturer"], row["MPN"], row["Distributor Part Number"])
        maximum[key] = int(row["Quantity"])
    rows, issues = [], []
    now = datetime.datetime.now(datetime.timezone.utc)
    for (manufacturer, mpn, route), quantity in sorted(maximum.items(), key=lambda item: item[0][2]):
        required = math.ceil(quantity * 1.10)
        if route.startswith("C") and route[1:].isdigit():
            available, observed_mpn, url = lcsc(route)
            if observed_mpn.casefold() != mpn.casefold():
                issues.append(f"{route} resolves to {observed_mpn}, expected {mpn}")
            source = "LCSC"
            observed_utc = now.isoformat()
        elif route in DIGIKEY:
            available, url = DIGIKEY[route]["available"], DIGIKEY[route]["url"]
            source = "DigiKey"
            observed_utc = DIGIKEY[route]["observed_utc"]
        else:
            issues.append(f"no live-stock route for {route}")
            available, url, source, observed_utc = 0, "", None, None
        passed = available >= required
        if not passed:
            issues.append(f"{route} has {available}, needs {required} including 10% overage")
        rows.append({
            "manufacturer": manufacturer, "mpn": mpn, "route": route,
            "panel_quantity_max": quantity, "required_with_10_percent_overage": required,
            "available_snapshot": available, "observed_utc": observed_utc,
            "passed": passed, "source": source, "url": url,
        })
    result = {
        "checked_utc": now.isoformat(),
        "policy": {"population_scope": "B-4n7-0p8", "overage_percent": 10},
        "passed": not issues, "issues": issues, "parts": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": not issues, "parts": len(rows), "issues": issues}, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
