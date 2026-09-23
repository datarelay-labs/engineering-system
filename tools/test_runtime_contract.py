#!/usr/bin/env python3
"""Deterministic regressions for the optional runtime/observability contract."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "runtime-contract.py"

_SPEC = importlib.util.spec_from_file_location("runtime_contract", TOOL)
assert _SPEC and _SPEC.loader
contract = importlib.util.module_from_spec(_SPEC)
sys.modules["runtime_contract"] = contract
_SPEC.loader.exec_module(contract)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def write_profiles(root: Path, health: str = "", smoke: str = "", e2e: str = "") -> None:
    engineering = root / ".engineering"
    engineering.mkdir(parents=True, exist_ok=True)
    (engineering / "project.yaml").write_text(
        yaml.safe_dump(
            {"operations": {"health_command": health, "production_oriented": False}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (engineering / "release.yaml").write_text(
        yaml.safe_dump(
            {
                "operational_e2e_command": e2e,
                "public_smoke_command": smoke,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    schema = root / "schemas"
    schema.mkdir(parents=True, exist_ok=True)
    (schema / "runtime-contract.schema.json").write_text(
        (ROOT / "schemas" / "runtime-contract.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def write_contract(root: Path, payload: dict) -> None:
    (root / ".engineering" / "runtime.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def unsupported_contract(**overrides: dict) -> dict:
    capabilities = {
        name: {"support": "unsupported"}
        for name in ("start", "logs", "browser", "metrics", "traces", "cleanup")
    }
    capabilities.update(overrides)
    return {
        "version": 1,
        "authorities": {
            "health": "operations.health_command",
            "smoke": "release.public_smoke_command",
            "e2e": "release.operational_e2e_command",
        },
        "capabilities": capabilities,
    }


def test_absent_contract_passes_and_reports_authorities() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="curl -fsS http://127.0.0.1/health", smoke="", e2e="make e2e")
        report = contract.check_contract(root)
    assert report["runtime_contract"] == "ABSENT"
    assert report["result"] == "PASS"
    assert report["findings"] == []
    assert report["authorities"]["health"]["support"] == "SUPPORTED"
    assert report["authorities"]["health"]["command"] == "curl -fsS http://127.0.0.1/health"
    assert report["authorities"]["health"]["field"] == "operations.health_command"
    assert report["authorities"]["smoke"]["support"] == "UNSUPPORTED"
    assert report["authorities"]["e2e"]["command"] == "make e2e"
    assert all(item["support"] == "UNSPECIFIED" for item in report["capabilities"].values())


def test_present_contract_does_not_execute_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="true", smoke="true", e2e="true")
        marker = root / "should-not-run"
        write_contract(
            root,
            unsupported_contract(
                logs={
                    "support": "supported",
                    "scope": "worktree",
                    "command": f"touch {marker.name}",
                    "fuller_command": "echo fuller",
                }
            ),
        )
        report = contract.check_contract(root)
        assert report["result"] == "PASS", report
        assert not marker.exists()
        rendered = run_cli("check", "--root", str(root))
        assert rendered.returncode == 0, rendered.stdout
        assert "RUNTIME_CONTRACT=PRESENT" in rendered.stdout
        assert "HEALTH_AUTHORITY=operations.health_command" in rendered.stdout
        assert "HEALTH_COMMAND=true" in rendered.stdout
        assert "CAPABILITY=logs SUPPORTED" in rendered.stdout
        assert "CAPABILITY_FULLER=logs echo fuller" in rendered.stdout
        assert "health_command:" not in (root / ".engineering" / "runtime.yaml").read_text(encoding="utf-8")


def test_duplicate_authority_command_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="make health", smoke="make smoke", e2e="make e2e")
        write_contract(
            root,
            unsupported_contract(
                start={"support": "supported", "scope": "worktree", "command": "make health"}
            ),
        )
        report = contract.check_contract(root)
    assert report["result"] == "FAIL"
    assert report["findings"] == [{"code": "DUPLICATE_AUTHORITY", "detail": "start"}]
    assert report["authorities"]["health"]["field"] == "operations.health_command"


def test_second_health_field_fails_schema() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root)
        payload = unsupported_contract(
            browser={
                "support": "supported",
                "scope": "worktree",
                "command": "curl -fsS http://127.0.0.1:8080/metrics",
                "fuller_command": "echo ok",
            }
        )
        payload["health_command"] = "invented"
        write_contract(root, payload)
        report = contract.check_contract(root)
    assert report["result"] == "FAIL"
    assert any(item["code"] == "SCHEMA" for item in report["findings"])
    assert report["authorities"]["health"]["support"] == "UNSUPPORTED"
    assert report["authorities"]["health"]["command"] == ""


def test_local_metrics_url_is_structurally_valid_and_not_executed() -> None:
    command = "curl -fsS http://127.0.0.1:8080/metrics"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="true", smoke="true", e2e="true")
        write_contract(
            root,
            unsupported_contract(
                metrics={
                    "support": "supported",
                    "scope": "worktree",
                    "command": command,
                    "fuller_command": 'echo "$METRICS" && curl -fsS http://127.0.0.1:8080/metrics',
                }
            ),
        )
        import os

        executed: list[object] = []

        def reject(*args: object, **kwargs: object) -> None:
            executed.append(args)
            raise AssertionError("runtime command executed")

        saved = (
            os.system,
            os.popen,
            subprocess.run,
            subprocess.Popen,
            subprocess.call,
            subprocess.check_call,
            subprocess.check_output,
        )
        os.system = reject
        os.popen = reject
        subprocess.run = reject
        subprocess.Popen = reject
        subprocess.call = reject
        subprocess.check_call = reject
        subprocess.check_output = reject
        try:
            report = contract.check_contract(root)
        finally:
            (
                os.system,
                os.popen,
                subprocess.run,
                subprocess.Popen,
                subprocess.call,
                subprocess.check_call,
                subprocess.check_output,
            ) = saved
        assert executed == []
        assert report["result"] == "PASS", report
        assert report["findings"] == []
        assert report["capabilities"]["metrics"]["command"] == command
        rendered = run_cli("check", "--root", str(root))
    assert rendered.returncode == 0, rendered.stdout
    assert f"CAPABILITY_COMMAND=metrics {command}" in rendered.stdout
    assert "UNSAFE_COMMAND" not in rendered.stdout
    assert "FINDING_COUNT=0" in rendered.stdout
    assert "command_problem" not in TOOL.read_text(encoding="utf-8")


def test_whitespace_only_command_fails_schema() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="true", smoke="true", e2e="true")
        write_contract(
            root,
            unsupported_contract(
                start={"support": "supported", "scope": "worktree", "command": " "},
                metrics={
                    "support": "supported",
                    "scope": "worktree",
                    "command": " ",
                    "fuller_command": " ",
                },
            ),
        )
        report = contract.check_contract(root)
    assert report["result"] == "FAIL"
    assert any(item["code"] == "SCHEMA" for item in report["findings"])
    assert all(item["code"] != "UNSAFE_COMMAND" for item in report["findings"])


def test_missing_authority_file_fails_closed_when_contract_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root)
        (root / ".engineering" / "project.yaml").unlink()
        write_contract(root, unsupported_contract())
        report = contract.check_contract(root)
    assert report["result"] == "FAIL"
    assert report["findings"][0]["code"] == "MISSING_AUTHORITY"


def test_bounded_output_keeps_fuller_and_raw_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profiles(root, health="make health", smoke="make smoke")
        write_contract(
            root,
            unsupported_contract(
                start={"support": "supported", "scope": "worktree", "command": "make health"},
                logs={
                    "support": "supported",
                    "scope": "worktree",
                    "command": "make health",
                    "fuller_command": "make smoke",
                },
            ),
        )
        bounded = run_cli("check", "--root", str(root), "--max-findings", "1")
        full = run_cli("check", "--root", str(root), "--full")
        raw = run_cli("check", "--root", str(root), "--raw")
    assert bounded.returncode == 1
    assert "FINDINGS_TRUNCATED=" in bounded.stdout
    assert bounded.stdout.count("FINDING=") == 1
    assert f"FULLER=python3 tools/runtime-contract.py check --full --root {root}" in bounded.stdout
    assert f"RAW=python3 tools/runtime-contract.py check --raw --root {root}" in bounded.stdout
    assert full.stdout.count("FINDING=") >= 2
    assert "FINDINGS_TRUNCATED" not in full.stdout
    payload = json.loads(raw.stdout)
    assert payload["result"] == "FAIL"
    assert len(payload["findings"]) >= 2


def test_canonical_contract_passes() -> None:
    result = run_cli("check", "--root", str(ROOT))
    assert result.returncode == 0, result.stdout
    assert "RUNTIME_CONTRACT=PRESENT" in result.stdout
    assert "RESULT=PASS" in result.stdout
    assert "HEALTH_SUPPORT=UNSUPPORTED" in result.stdout
    assert "SMOKE_SUPPORT=UNSUPPORTED" in result.stdout
    assert "E2E_SUPPORT=UNSUPPORTED" in result.stdout
    assert "CAPABILITY=start UNSUPPORTED" in result.stdout
    assert "CAPABILITY=cleanup UNSUPPORTED" in result.stdout


def main() -> int:
    test_absent_contract_passes_and_reports_authorities()
    test_present_contract_does_not_execute_commands()
    test_duplicate_authority_command_fails()
    test_second_health_field_fails_schema()
    test_local_metrics_url_is_structurally_valid_and_not_executed()
    test_whitespace_only_command_fails_schema()
    test_missing_authority_file_fails_closed_when_contract_present()
    test_bounded_output_keeps_fuller_and_raw_paths()
    test_canonical_contract_passes()
    print("RUNTIME_CONTRACT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
