#!/usr/bin/env python3
"""Positive and mutation-negative tests for the frozen-candidate HDI gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VALIDATOR = ROOT / "validate_stacks.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(project: Path, manifest: Path, drc: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(project), "--manifest", str(manifest), "--drc-json", str(drc)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--drc-json", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    BASE_PROJECT, BASE_DRC = args.project, args.drc_json
    work = Path(tempfile.mkdtemp(prefix="run-"))
    results: dict[str, dict] = {}
    failures: list[str] = []

    def case(name: str, mutator, expected: str) -> None:
        case_root = work / name
        project = case_root / "project"
        shutil.copytree(BASE_PROJECT, project)
        drc = case_root / "drc.json"
        shutil.copy2(BASE_DRC, drc)
        manifest_path = project / "stack-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        report = json.loads(drc.read_text())
        mutator(project, manifest, report)
        write_json(manifest_path, manifest)
        write_json(drc, report)
        code, output = run(project, manifest_path, drc)
        passed = code != 0 and expected in output
        results[name] = {"pass": passed, "expected": expected, "exit": code, "output": output.strip().splitlines()[:8]}
        if not passed:
            failures.append(name)

    code, output = run(BASE_PROJECT, BASE_PROJECT / "stack-manifest.json", BASE_DRC)
    positive = code == 0 and "PASS: fail-closed HDI manifest" in output
    results["positive"] = {"pass": positive, "exit": code, "output": output.strip().splitlines()}
    if not positive:
        failures.append("positive")

    case(
        "missing-manifest-tier",
        lambda _p, m, _d: m["stacks"][0]["tiers"].pop(),
        "unmanifested board microvia",
    )
    case(
        "wrong-center",
        lambda _p, m, _d: m["stacks"][0]["tiers"][0]["center_mm"].__setitem__(0, m["stacks"][0]["tiers"][0]["center_mm"][0] + 0.01),
        "center",
    )
    case(
        "wrong-net",
        lambda _p, m, _d: m["stacks"][0].__setitem__("net", "WRONG_NET"),
        "manifest 'WRONG_NET'",
    )
    case(
        "wrong-layer-pair",
        lambda _p, m, _d: m["stacks"][0]["tiers"][0].__setitem__("layers", ["F.Cu", "In2.Cu"]),
        "illegal adjacent pair",
    )
    case(
        "wrong-pad-geometry",
        lambda _p, m, _d: m["stacks"][0]["tiers"][0].__setitem__("pad_mm", 0.4),
        "pad",
    )
    case(
        "wrong-drill-geometry",
        lambda _p, m, _d: m["stacks"][0]["tiers"][0].__setitem__("hole_mm", 0.2),
        "drill",
    )
    case(
        "missing-layer-stack",
        lambda _p, m, _d: m.pop("layer_stack"),
        "manifest missing layer_stack",
    )
    case(
        "wrong-layer-stack-thickness",
        lambda _p, m, _d: m["layer_stack"]["rows"][1].__setitem__("thickness_mm", 0.077),
        "layer stack differs from exact manifest",
    )

    def duplicate_uuid(_project: Path, manifest: dict, _report: dict) -> None:
        manifest["stacks"][1]["tiers"][0]["via_uuid"] = manifest["stacks"][0]["tiers"][0]["via_uuid"]

    case("duplicate-manifest-uuid", duplicate_uuid, "appears in both")

    def split_chain(_project: Path, manifest: dict, _report: dict) -> None:
        stack = next(stack for stack in manifest["stacks"] if len(stack["tiers"]) == 3)
        tier = stack["tiers"].pop()
        manifest["stacks"].append({"name": "illegally-split-tier", "net": stack["net"], "face": stack["face"], "tiers": [tier]})

    case("split-physical-chain", split_chain, "not in one approved stack")

    def stack_markers(manifest: dict, report: dict) -> list[dict]:
        approved = {tuple(sorted(marker["via_uuids"])) for marker in manifest["allowed_drc_markers"]}
        return [
            violation
            for violation in report["violations"]
            if not violation.get("excluded")
            and tuple(sorted(item["uuid"] for item in violation["items"])) in approved
        ]

    def stale_drc(_project: Path, manifest: dict, report: dict) -> None:
        report["violations"].remove(stack_markers(manifest, report)[0])

    case("stale-drc-multiset", stale_drc, "missing expected active stack marker")

    def extra_stack_report(_project: Path, manifest: dict, report: dict) -> None:
        report["violations"].append(json.loads(json.dumps(stack_markers(manifest, report)[0])))

    case("extra-stack-report", extra_stack_report, "unexpected active stack marker")

    def unsupported_type(_project: Path, manifest: dict, report: dict) -> None:
        injected = json.loads(json.dumps(stack_markers(manifest, report)[0]))
        injected["type"] = "silk_over_copper"
        report["violations"].append(injected)

    case("active-unsupported-type", unsupported_type, "unexpected active DRC marker")

    def unreviewed_board_rule(_project: Path, manifest: dict, report: dict) -> None:
        injected = json.loads(json.dumps(stack_markers(manifest, report)[0]))
        injected["items"][0]["uuid"] = "00000000-0000-4000-8000-000000000001"
        injected["description"] = "Clearance violation (rule 'unreviewed injected rule' clearance 0.1000 mm; actual 0.0000 mm)"
        report["violations"].append(injected)

    case("unreviewed-board-rule", unreviewed_board_rule, "unreviewed active board marker rule")

    def stale_board_rule(_project: Path, manifest: dict, _report: dict) -> None:
        manifest["reviewed_board_markers"]["rows"].append(
            {"type": "clearance", "rule": "rule 'rule that reports nothing'", "observed_count": 1}
        )

    case("stale-board-rule", stale_board_rule, "reports nothing; manifest is stale")

    def wrong_drill_separation(_project: Path, manifest: dict, _report: dict) -> None:
        manifest["via_drill_separation_mm"]["same_net"] = 0.02

    case("wrong-drill-separation", wrong_drill_separation, "via_drill_separation_mm[same_net]")

    def wrong_excluded_marker(_project: Path, _manifest: dict, report: dict) -> None:
        violation = next(v for v in report["violations"] if v.get("excluded"))
        violation["items"][0]["uuid"] = "00000000-0000-4000-8000-000000000000"

    case("wrong-excluded-drc-signature", wrong_excluded_marker, "excluded-marker signatures differ")

    def broad_ignore(project: Path, manifest: dict, _report: dict) -> None:
        rules = project / "meenie.kicad_dru"
        rules.write_text(rules.read_text() + "\n(rule \"bad broad ignore\" (severity ignore) (condition \"A.Type == 'Via'\"))\n")
        manifest["source_hashes"]["rules_sha256"] = sha256(rules)

    case("broad-ignore-rule", broad_ignore, "broad severity-ignore rule")

    def extra_native_exclusion(project: Path, manifest: dict, _report: dict) -> None:
        pro = project / "meenie.kicad_pro"
        data = json.loads(pro.read_text())
        data["board"]["design_settings"]["drc_exclusions"].append(["clearance|1|1|fake|fake", "negative test"])
        write_json(pro, data)
        manifest["source_hashes"]["project_sha256"] = sha256(pro)

    case("extra-native-exclusion", extra_native_exclusion, "project DRC exclusions differ")

    case(
        "missing-central-transition",
        lambda _p, m, _d: m["central_buried_transitions"].pop(),
        "unmanifested board blind/buried via",
    )
    case(
        "wrong-central-center",
        lambda _p, m, _d: m["central_buried_transitions"][0]["center_mm"].__setitem__(0, m["central_buried_transitions"][0]["center_mm"][0] + 0.01),
        "central transition",
    )
    case(
        "wrong-central-pair",
        lambda _p, m, _d: m["central_buried_transitions"][0].__setitem__("layers", ["In2.Cu", "In3.Cu"]),
        "illegal pair",
    )
    case(
        "missing-central-capture-link",
        lambda _p, m, _d: m["central_capture_links"].pop(),
        "missing central capture link",
    )

    result_path = args.out
    result_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(result_path, {"all_pass": not failures, "case_count": len(results),
                             "failures": failures, "cases": results})
    shutil.rmtree(work)
    print(json.dumps({"all_pass": not failures, "case_count": len(results), "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
