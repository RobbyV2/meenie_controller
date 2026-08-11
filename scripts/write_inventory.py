#!/usr/bin/env python3
import argparse, hashlib, json, os, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=pathlib.Path)
    args = parser.parse_args()
    root = args.root.resolve()
    checksum = root / "reports" / "SHA256SUMS.txt"
    inventory_path = root / "reports" / "artifact-inventory.json"
    excluded = {checksum, inventory_path}
    files = [path for path in root.rglob("*") if path.is_file() and path not in excluded]
    records = [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256(path)}
               for path in sorted(files)]
    checksum.write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in records))
    by_top = {}
    for item in records:
        top = item["path"].split("/", 1)[0]
        row = by_top.setdefault(top, {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += item["bytes"]
    uploads = [item["path"] for item in records if item["path"].endswith(("upload.zip", "assembly-variants.zip"))]
    inventory = {
        "root": os.path.relpath(root, REPO),
        "files_excluding_inventory_and_checksum": len(records),
        "bytes_excluding_inventory_and_checksum": sum(item["bytes"] for item in records),
        "by_top_directory": by_top,
        "upload_archives": uploads,
        "orderable": json.loads((root / "reports" / "release.json").read_text())["orderable"],
        "warning": "No upload archive may be created or sent until the signed NextPCB DFM/quote JSON passes the exact release contract.",
        "files": records,
    }
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: inventory[key] for key in ("files_excluding_inventory_and_checksum", "bytes_excluding_inventory_and_checksum", "by_top_directory", "upload_archives")}, indent=2))


if __name__ == "__main__":
    main()
