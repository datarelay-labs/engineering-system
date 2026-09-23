#!/usr/bin/env python3
"""Deterministic regressions for optional knowledge freshness and retrieval routing."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "knowledge-contract.py"

_SPEC = importlib.util.spec_from_file_location("knowledge_contract", TOOL)
assert _SPEC and _SPEC.loader
contract = importlib.util.module_from_spec(_SPEC)
sys.modules["knowledge_contract"] = contract
_SPEC.loader.exec_module(contract)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def write_index(root: Path, payload: dict) -> None:
    path = root / ".engineering"
    path.mkdir(parents=True, exist_ok=True)
    (path / "knowledge.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    schema = root / "schemas"
    schema.mkdir(parents=True, exist_ok=True)
    (schema / "knowledge-index.schema.json").write_text(
        (ROOT / "schemas" / "knowledge-index.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def base_index(**extra: object) -> dict:
    payload = {
        "version": 1,
        "domains": [
            {
                "id": "core",
                "summary": "Canonical source.",
                "canonical": ["standards/CORE.md"],
            }
        ],
    }
    payload.update(extra)
    return payload


def seed_repo(root: Path) -> None:
    target = root / "standards"
    target.mkdir(parents=True)
    (target / "CORE.md").write_text("canonical\n", encoding="utf-8")


def codes(report: dict) -> list[str]:
    return [item["code"] for item in report["findings"]]


def test_absent_index_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = contract.check_index(Path(tmp))
    assert report["knowledge_index"] == "ABSENT"
    assert report["result"] == "PASS"
    assert report["findings"] == []


def test_present_valid_index_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed_repo(root)
        write_index(root, base_index())
        report = contract.check_index(root)
    assert report == {"knowledge_index": "PRESENT", "result": "PASS", "findings": []}


def test_missing_canonical_reference_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_index(root, base_index())
        report = contract.check_index(root)
    assert report["result"] == "FAIL"
    assert codes(report) == ["MISSING"]
    assert report["findings"][0]["detail"] == "standards/CORE.md"


def test_source_of_truth_conflict_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed_repo(root)
        write_index(
            root,
            base_index(derived=[{"path": "standards/CORE.md", "note": "duplicate authority"}]),
        )
        report = contract.check_index(root)
    assert codes(report) == ["CONFLICT"]


def test_stale_generated_reference_fixture() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed_repo(root)
        source = root / "standards" / "CORE.md"
        generated = root / "docs" / "core.generated.md"
        generated.parent.mkdir()
        generated.write_text("generated copy\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        stale = "0" * 64
        payload = base_index(
            generated=[
                {
                    "path": "docs/core.generated.md",
                    "source": "standards/CORE.md",
                    "source_sha256": stale,
                }
            ]
        )
        write_index(root, payload)
        stale_report = contract.check_index(root)
        assert codes(stale_report) == ["STALE_GENERATED"]
        payload["generated"][0]["source_sha256"] = digest
        write_index(root, payload)
        fresh = contract.check_index(root)
    assert fresh["result"] == "PASS"


def test_unsafe_path_does_not_escape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outside = root.parent / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        write_index(
            root,
            base_index(
                domains=[
                    {
                        "id": "core",
                        "summary": "Escape attempt.",
                        "canonical": ["../outside.md"],
                    }
                ]
            ),
        )
        report = contract.check_index(root)
    assert codes(report) == ["UNSAFE_PATH"]
    assert "secret" not in json.dumps(report)


def test_bounded_output_keeps_raw_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        groups = [
            [f"missing-a-{index}.md" for index in range(12)],
            [f"missing-b-{index}.md" for index in range(12)],
            ["missing-c-0.md"],
        ]
        write_index(
            root,
            {
                "version": 1,
                "domains": [
                    {"id": f"group-{index}", "summary": "Missing canonical paths.", "canonical": paths}
                    for index, paths in enumerate(groups)
                ],
            },
        )
        bounded = run_cli("check", "--root", str(root))
        raw = run_cli("check", "--root", str(root), "--raw")
        full = run_cli("check", "--root", str(root), "--full")
    assert bounded.returncode == 1
    assert "FINDINGS_TRUNCATED=5" in bounded.stdout
    assert bounded.stdout.count("FINDING=") == 20
    assert f"RAW=python3 tools/knowledge-contract.py check --raw --root {root}" in bounded.stdout
    assert f"FULLER=python3 tools/knowledge-contract.py check --full --root {root}" in bounded.stdout
    payload = json.loads(raw.stdout)
    assert len(payload["findings"]) == 25
    assert full.stdout.count("FINDING=") == 25
    assert "FINDINGS_TRUNCATED" not in full.stdout


def test_route_stays_local_without_evidence() -> None:
    result = run_cli("route")
    assert result.returncode == 0, result.stdout
    assert "RETRIEVAL=LOCAL" in result.stdout
    assert "REASON_COUNT=0" in result.stdout


def test_route_escalates_only_on_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        local = base / "local.yaml"
        local.write_text(
            yaml.safe_dump({"files_consulted": 3, "repos": ["datarelay-labs/engineering-system"], "reread": {"standards/CORE.md": 1}}),
            encoding="utf-8",
        )
        breadth = base / "breadth.yaml"
        breadth.write_text(yaml.safe_dump({"files_consulted": contract.BREADTH_FILES}), encoding="utf-8")
        cross = base / "cross.yaml"
        cross.write_text(
            yaml.safe_dump({"repos": ["datarelay-labs/engineering-system", "datarelay-labs/other"]}),
            encoding="utf-8",
        )
        reread = base / "reread.yaml"
        reread.write_text(
            yaml.safe_dump({"reread": {"standards/CORE.md": contract.REREAD_THRESHOLD}}),
            encoding="utf-8",
        )
        local_result = run_cli("route", "--signals", str(local))
        breadth_result = run_cli("route", "--signals", str(breadth), "--raw")
        cross_result = run_cli("route", "--signals", str(cross), "--raw")
        reread_result = run_cli("route", "--signals", str(reread), "--raw")
    assert "RETRIEVAL=LOCAL" in local_result.stdout
    assert json.loads(breadth_result.stdout)["reasons"] == ["breadth"]
    assert json.loads(cross_result.stdout)["reasons"] == ["cross_repo"]
    assert json.loads(reread_result.stdout)["reasons"] == ["repeated_reread"]


def test_invalid_signals_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "signals.yaml"
        path.write_text("files_consulted: -1\n", encoding="utf-8")
        result = run_cli("route", "--signals", str(path))
    assert result.returncode == 1
    assert "RETRIEVAL=BLOCKED" in result.stdout
    assert "REASON=INVALID_SIGNALS" in result.stdout


def test_tool_has_no_retrieval_vendor() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for token in ("urllib", "requests", "socket", "sourcegraph", "openai"):
        assert token not in text


def test_canonical_index_passes() -> None:
    result = run_cli("check", "--root", str(ROOT))
    assert result.returncode == 0, result.stdout
    assert "KNOWLEDGE_INDEX=PRESENT" in result.stdout
    assert "RESULT=PASS" in result.stdout


def main() -> int:
    subprocess.run([sys.executable, "-m", "py_compile", str(TOOL)], check=True)
    test_absent_index_passes()
    test_present_valid_index_passes()
    test_missing_canonical_reference_fails()
    test_source_of_truth_conflict_fails()
    test_stale_generated_reference_fixture()
    test_unsafe_path_does_not_escape()
    test_bounded_output_keeps_raw_path()
    test_route_stays_local_without_evidence()
    test_route_escalates_only_on_evidence()
    test_invalid_signals_fail_closed()
    test_tool_has_no_retrieval_vendor()
    test_canonical_index_passes()
    print("KNOWLEDGE_CONTRACT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
