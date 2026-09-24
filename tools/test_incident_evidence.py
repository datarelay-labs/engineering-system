#!/usr/bin/env python3
"""Deterministic regressions for incident packets and core evidence capture."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "incident-evidence.py"
SCHEMA = ROOT / "schemas" / "incident-evidence.schema.json"

_SPEC = importlib.util.spec_from_file_location("incident_evidence", TOOL)
assert _SPEC and _SPEC.loader
incident = importlib.util.module_from_spec(_SPEC)
sys.modules["incident_evidence"] = incident
_SPEC.loader.exec_module(incident)

INCIDENT_ID = "INC-20260924-host-pressure"
CAPTURE_ID = "CAP-20260924T010203Z-a1b2c3d4"
NOW = "2026-09-24T01:02:03Z"
SECRET_NAME = "secret-filename-do-not-capture.txt"
SECRET_BODY = "SECRET_CONTENT_DO_NOT_CAPTURE"
TOKEN = "supersecret-token-value"
SENTINEL = "SENTINEL-SECRET-VALUE"
SESSION_NAME = "secret-chat-id"
EMAIL = "incident-evidence-test@example.com"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["INCIDENT_EVIDENCE_SENTINEL"] = SENTINEL
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=merged,
    )


def solo_packet(**overrides: str) -> str:
    headers = {
        "INCIDENT_PACKET_VERSION": "1",
        "TARGET_REPO": "datarelay-labs/engineering-system",
        "INCIDENT_ID": INCIDENT_ID,
        "INCIDENT_PHASE": "STABILIZING",
        "SEVERITY": "HIGH",
        "IMPACT_STATE": "ONGOING",
        "DETECTED_AT": "2026-09-24T01:00:00Z",
        "LAST_UPDATED_AT": "2026-09-24T01:10:00Z",
        "INCIDENT_COMMANDER": "solo-operator",
        "OPS_OWNER": "SAME_AS_IC",
        "COMMS_OWNER": "N/A",
        "SAFETY_FREEZE": "ON",
        "LAST_EVIDENCE_CAPTURE": "NONE",
    }
    headers.update(overrides)
    sections = {
        "Current Impact": "UNKNOWN",
        "Stabilization / Safety State": "SAFETY_FREEZE narrows execution. No session mutation.",
        "Evidence": "NONE",
        "Current Hypothesis": "Hypothesis only. Root cause is unproven.",
        "Mitigation / Rollback": "This packet does not authorize mitigation.",
        "Next Update / Blocker": "Capture core evidence before any mutation.",
        "Corrective Follow-ups": "NONE",
    }
    body = "\n".join(f"{key}={value}" for key, value in headers.items())
    rendered = [body, ""]
    for name, text in sections.items():
        rendered.append(f"## {name}")
        rendered.append("")
        rendered.append(text)
        rendered.append("")
    return "\n".join(rendered)


def write_packet(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def git_repo(root: Path, origin: str) -> None:
    subprocess.run(["git", "init", "-b", "evidence-fixture"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", EMAIL], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Incident Evidence Test"], cwd=root, check=True)
    (root / "README").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)


def git_dir(root: Path) -> Path:
    output = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
        text=True,
    )
    return Path(output.strip())


def head(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def fixtures(directory: Path) -> dict[str, str]:
    meminfo = directory / "meminfo"
    meminfo.write_text(
        "\n".join(
            [
                "MemTotal:       33554432 kB",
                "MemAvailable:     262144 kB",
                "SwapTotal:       8388608 kB",
                "SwapFree:        1048576 kB",
                "",
            ]
        ),
        encoding="utf-8",
    )
    psi_memory = directory / "psi-memory"
    psi_memory.write_text(
        "some avg10=80.00 avg60=70.00 avg300=40.00 total=12345\n"
        "full avg10=40.00 avg60=30.00 avg300=10.00 total=6789\n",
        encoding="utf-8",
    )
    psi_io = directory / "psi-io"
    psi_io.write_text(
        "some avg10=25.00 avg60=11.00 avg300=4.00 total=222\n"
        "full avg10=9.00 avg60=3.00 avg300=1.00 total=111\n",
        encoding="utf-8",
    )
    loadavg = directory / "loadavg"
    loadavg.write_text("0.50 1.25 2.50\n", encoding="utf-8")
    sessions = directory / "sessions"
    rows = "\n".join(f"  Session: {SESSION_NAME}-{index}" for index in range(12))
    sessions.write_text(f"12 persistent sessions:\n{rows}\n", encoding="utf-8")
    return {
        "meminfo": str(meminfo),
        "psi_memory": str(psi_memory),
        "psi_io": str(psi_io),
        "loadavg": str(loadavg),
        "sessions": str(sessions),
    }


def capture_args(root: Path, files: dict[str, str], **extra: str) -> list[str]:
    args = [
        "capture",
        "--root",
        str(root),
        "--incident-id",
        extra.get("incident_id", INCIDENT_ID),
        "--now",
        NOW,
        "--capture-id",
        extra.get("capture_id", CAPTURE_ID),
        "--meminfo-file",
        files["meminfo"],
        "--psi-memory-file",
        files["psi_memory"],
        "--psi-io-file",
        files["psi_io"],
        "--loadavg-file",
        files["loadavg"],
        "--persist-list-file",
        files["sessions"],
    ]
    return args


def evidence_path(root: Path, capture_id: str = CAPTURE_ID) -> Path:
    return git_dir(root) / "engineering-system" / "incidents" / INCIDENT_ID / f"{capture_id}.json"


def test_packet_accepts_solo_operator_and_grants_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "packet.md"
        write_packet(path, solo_packet())
        result = run_cli("check-packet", "--packet-file", str(path))
    assert result.returncode == 0, result.stdout
    assert "RESULT=PASS" in result.stdout
    assert "AUTHORITY=NONE" in result.stdout
    assert "SAFETY_FREEZE=ON" in result.stdout
    assert "SAFETY_EFFECT=NARROW" in result.stdout
    assert "SAFETY_EFFECT=GRANT" not in result.stdout
    assert "AUTHORITY=GRANTED" not in result.stdout


def test_packet_rejects_missing_unknown_and_contradictions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        missing = directory / "missing.md"
        write_packet(missing, "\n".join(line for line in solo_packet().splitlines() if not line.startswith("SEVERITY=")))
        unknown = directory / "unknown.md"
        write_packet(unknown, solo_packet(SEVERITY="EXTREME"))
        contradiction = directory / "contradiction.md"
        write_packet(contradiction, solo_packet(INCIDENT_PHASE="RESOLVED", IMPACT_STATE="ONGOING"))
        stale = directory / "stale.md"
        write_packet(stale, solo_packet(LAST_UPDATED_AT="2026-09-24T00:00:00Z"))
        grant = directory / "grant.md"
        write_packet(grant, solo_packet() + "AUTHORIZE_MITIGATION=YES\n")
        traversal = directory / "traversal.md"
        write_packet(traversal, solo_packet(INCIDENT_ID="INC-20260924-../escape"))
        missing_result = run_cli("check-packet", "--packet-file", str(missing))
        unknown_result = run_cli("check-packet", "--packet-file", str(unknown))
        contradiction_result = run_cli("check-packet", "--packet-file", str(contradiction))
        stale_result = run_cli("check-packet", "--packet-file", str(stale))
        grant_result = run_cli("check-packet", "--packet-file", str(grant))
        traversal_result = run_cli("check-packet", "--packet-file", str(traversal))
        sentinel = directory / "sentinel-touched"
        injected = directory / "injected.md"
        write_packet(injected, solo_packet() + f"$(touch {sentinel})\n")
        injected_result = run_cli("check-packet", "--packet-file", str(injected))
        assert not sentinel.exists()
    assert missing_result.returncode == 2
    assert "REASON=MISSING_HEADER:SEVERITY" in missing_result.stdout
    assert "AUTHORITY=NONE" in missing_result.stdout
    assert "REASON=UNKNOWN_VALUE:SEVERITY" in unknown_result.stdout
    assert "REASON=PHASE_IMPACT_CONTRADICTION" in contradiction_result.stdout
    assert "REASON=UPDATED_BEFORE_DETECTED" in stale_result.stdout
    assert "REASON=AUTHORITY_GRANT" in grant_result.stdout
    assert "REASON=UNKNOWN_VALUE:INCIDENT_ID" in traversal_result.stdout
    assert injected_result.returncode == 0


def test_freeze_off_does_not_grant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "packet.md"
        write_packet(path, solo_packet(SAFETY_FREEZE="OFF", INCIDENT_PHASE="MONITORING", IMPACT_STATE="CONTAINED"))
        result = run_cli("check-packet", "--packet-file", str(path))
    assert result.returncode == 0, result.stdout
    assert "AUTHORITY=NONE" in result.stdout
    assert "SAFETY_EFFECT=NONE" in result.stdout


def test_historical_pressure_fixture_states_facts_without_root_cause() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(outside)
        result = run_cli(*capture_args(root, files))
        record_path = evidence_path(root)
        payload = record_path.read_text(encoding="utf-8")
        record = json.loads(payload)
    assert result.returncode == 0, result.stdout
    assert "RESULT=COMPLETE" in result.stdout
    assert "ROOT_CAUSE=UNPROVEN" in result.stdout
    assert "AUTHORITY=NONE" in result.stdout
    assert record["pressure_facts"] == [
        "LOW_MEM_AVAILABLE",
        "MATERIAL_SWAP_USE",
        "ELEVATED_MEMORY_PRESSURE",
        "ELEVATED_IO_PRESSURE",
        "HIGH_PERSISTENT_SESSION_COUNT",
    ]
    assert record["memory"]["available_bytes"] == 262144 * 1024
    assert record["swap"]["used_bytes"] == (8388608 - 1048576) * 1024
    assert record["sessions"]["persistent_count"] == 12
    assert record["psi_memory"]["some_avg10"] == 80.0
    assert record["psi_io"]["some_avg10"] == 25.0
    assert "root_cause" not in payload
    assert incident.validate_record(record) == []


def test_capture_is_schema_valid_real_git_and_redacted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        origin = f"https://user:{TOKEN}@github.com/datarelay-labs/engineering-system.git"
        git_repo(root, origin)
        (root / SECRET_NAME).write_text(SECRET_BODY, encoding="utf-8")
        files = fixtures(outside)
        result = run_cli(*capture_args(root, files))
        record_path = evidence_path(root)
        payload = record_path.read_text(encoding="utf-8")
        record = json.loads(payload)
        mode = stat.S_IMODE(record_path.stat().st_mode)
        expected_head = head(root)
        root_text = str(root)
        git_text = str(git_dir(root))
    assert result.returncode == 0, result.stdout
    assert record["git"]["head"] == expected_head
    assert record["git"]["branch"] == "evidence-fixture"
    assert record["git"]["dirty"] is True
    assert record["git"]["changed_file_count"] == 1
    assert record["repository"]["identity"] == "datarelay-labs/engineering-system"
    assert record["overall"] == "COMPLETE"
    assert record["authority"] == "NONE"
    assert result.stdout.count("EVIDENCE_REF=engineering-system/incidents/") == 1
    assert root_text not in payload
    assert root_text not in result.stdout
    assert git_text not in payload
    assert git_text not in result.stdout
    for banned in (SECRET_NAME, SECRET_BODY, TOKEN, SENTINEL, SESSION_NAME, EMAIL, origin):
        assert banned not in payload
        assert banned not in result.stdout
    assert mode == 0o600
    assert incident.validate_record(record) == []


def test_missing_source_is_partial_without_fabricated_zero() -> None:
    absent = incident._psi(None, "ABSENT", True, "PSI_MEMORY_ABSENT", "PSI_MEMORY_UNPARSEABLE", "PSI_MEMORY_UNBOUNDED")
    unsupported = incident._memory_and_swap(None, None, False)
    assert absent == {"state": "UNAVAILABLE", "reason": "PSI_MEMORY_ABSENT"}
    assert "some_avg10" not in absent
    assert unsupported[0]["state"] == "UNSUPPORTED"
    assert "total_bytes" not in unsupported[0]
    assert "used_bytes" not in unsupported[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(outside)
        bad = outside / "bad-psi"
        bad.write_text("not-psi\n", encoding="utf-8")
        files["psi_memory"] = str(bad)
        result = run_cli(*capture_args(root, files))
        record = json.loads(evidence_path(root).read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stdout
    assert record["overall"] == "PARTIAL"
    assert record["psi_memory"] == {"state": "UNAVAILABLE", "reason": "PSI_MEMORY_UNPARSEABLE"}
    assert "some_avg10" not in record["psi_memory"]
    assert record["memory"]["available_bytes"] == 262144 * 1024


def test_session_file_seam_does_not_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        sentinel = Path(tmp) / "agent-ran"
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(outside)
        listed = run_cli(*capture_args(root, files, capture_id="CAP-20260924T010203Z-11111111"))
        listed_record = json.loads(
            evidence_path(root, "CAP-20260924T010203Z-11111111").read_text(encoding="utf-8")
        )
    assert listed.returncode == 0, listed.stdout
    assert listed_record["sessions"]["persistent_count"] == 12
    assert SESSION_NAME not in json.dumps(listed_record)
    assert not sentinel.exists()


def test_agent_bin_sentinel_is_not_executed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        sentinel = Path(tmp) / "agent-ran"
        program = Path(tmp) / "chosen-agent"
        program.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
        program.chmod(0o755)
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(outside)
        result = run_cli(
            "capture",
            "--root",
            str(root),
            "--incident-id",
            INCIDENT_ID,
            "--now",
            NOW,
            "--capture-id",
            CAPTURE_ID,
            "--meminfo-file",
            files["meminfo"],
            "--psi-memory-file",
            files["psi_memory"],
            "--psi-io-file",
            files["psi_io"],
            "--loadavg-file",
            files["loadavg"],
            "--persist-list-file",
            files["sessions"],
            "--agent-bin",
            str(program),
        )
        executed = sentinel.exists()
    assert result.returncode != 0
    assert not executed
    source = TOOL.read_text(encoding="utf-8")
    assert "--agent-bin" not in source
    assert 'read_persist_list("agent")' in source


def test_fsmonitor_sentinel_is_not_executed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        sentinel = Path(tmp) / "fsmonitor-ran"
        hook = Path(tmp) / "fsmonitor"
        hook.write_text(f"#!/bin/sh\ntouch {sentinel}\nprintf '0'\n", encoding="utf-8")
        hook.chmod(0o755)
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        (root / "dirty.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
        armed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=normal"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        assert armed.returncode == 0
        assert sentinel.exists()
        sentinel.unlink()
        files = fixtures(outside)
        result = run_cli(*capture_args(root, files))
        record = json.loads(evidence_path(root).read_text(encoding="utf-8"))
        executed = sentinel.exists()
    assert result.returncode == 0, result.stdout
    assert not executed
    assert record["git"]["dirty"] is True
    assert record["git"]["changed_file_count"] == 1
    assert "core.fsmonitor=false" in TOOL.read_text(encoding="utf-8")


def test_unavailable_agent_omits_count() -> None:
    preflight = incident.load_preflight()
    original = preflight.read_persist_list

    def fixed_only(agent_bin: str) -> str:
        if agent_bin != "agent":
            raise AssertionError(agent_bin)
        raise preflight.PreflightFailure("agent persist list unavailable")

    preflight.read_persist_list = fixed_only
    try:
        result = incident._sessions(None, None)
    finally:
        preflight.read_persist_list = original
    assert result == {"state": "UNAVAILABLE", "reason": "SESSION_LIST_UNAVAILABLE"}
    assert "persistent_count" not in result


def test_retention_bounds_and_traversal_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "outside"
        outside.mkdir()
        fixtures_dir = Path(tmp) / "fixtures"
        fixtures_dir.mkdir()
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(fixtures_dir)
        escaped = git_dir(root) / "engineering-system"
        escaped.symlink_to(outside, target_is_directory=True)
        escaped_result = run_cli(*capture_args(root, files))
        escaped.unlink()
        assert escaped_result.returncode == 2
        assert "REASON=RETENTION_PATH_ESCAPE" in escaped_result.stdout
        assert list(outside.iterdir()) == []
        bad_id = run_cli(*capture_args(root, files, incident_id="INC-20260924-../escape"))
        assert bad_id.returncode == 2
        assert "REASON=INCIDENT_ID_INVALID" in bad_id.stdout
        first = run_cli(*capture_args(root, files))
        assert first.returncode == 0, first.stdout
        original = evidence_path(root).read_bytes()
        collision = run_cli(*capture_args(root, files))
        assert collision.returncode == 2
        assert "REASON=CAPTURE_ID_COLLISION" in collision.stdout
        assert evidence_path(root).read_bytes() == original
        retention = evidence_path(root).parent
        marker = retention / "kept.json"
        marker.write_text('{"kept":true}\n', encoding="utf-8")
        for index in range(incident.MAX_CAPTURES_PER_INCIDENT - 1):
            (retention / f"extra-{index}.json").write_text("{}\n", encoding="utf-8")
        bounded = run_cli(*capture_args(root, files, capture_id="CAP-20260924T010203Z-bbbbbbbb"))
        assert bounded.returncode == 2
        assert "REASON=RETENTION_COUNT_BOUND" in bounded.stdout
        assert marker.read_text(encoding="utf-8") == '{"kept":true}\n'
        assert not (retention / "CAP-20260924T010203Z-bbbbbbbb.json").exists()
        for path in list(retention.glob("extra-*.json")):
            path.unlink()
        marker.write_bytes(b"x" * incident.MAX_INCIDENT_BYTES)
        sized = run_cli(*capture_args(root, files, capture_id="CAP-20260924T010203Z-cccccccc"))
        assert sized.returncode == 2
        assert "REASON=RETENTION_SIZE_BOUND" in sized.stdout
        assert marker.read_bytes() == b"x" * incident.MAX_INCIDENT_BYTES
        assert not (retention / "CAP-20260924T010203Z-cccccccc.json").exists()


def test_command_fields_cannot_execute() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sentinel = Path(tmp) / "executed"
        result = run_cli("capture", "--root", str(tmp), "--incident-id", INCIDENT_ID, "--command", f"touch {sentinel}")
        assert result.returncode != 0
        assert not sentinel.exists()
    source = TOOL.read_text(encoding="utf-8")
    for banned in (
        "shell=True",
        "os.system",
        "os.kill",
        "os.environ",
        "persist stop",
        "persist kill",
        "urlopen",
        "socket",
        "requests",
        "eval(",
        "exec(",
    ):
        assert banned not in source
    assert incident.normalize_remote(f"https://user:{TOKEN}@github.com/acme/widget.git") == "acme/widget"
    assert TOKEN not in str(incident.normalize_remote(f"https://user:{TOKEN}@github.com/acme/widget.git"))
    assert incident.count_porcelain_z(b" M file\0?? other\0") == 2
    assert incident.count_porcelain_z(b"R  old\0new\0") == 1


def test_schema_rejects_unsafe_record_shapes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        outside = Path(tmp) / "fixtures"
        outside.mkdir()
        git_repo(root, "https://github.com/datarelay-labs/engineering-system.git")
        files = fixtures(outside)
        result = run_cli(*capture_args(root, files))
        record = json.loads(evidence_path(root).read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stdout
    leaked = dict(record)
    leaked["repository"] = {"state": "CAPTURED", "reason": "OK", "identity": str(root)}
    assert incident.validate_record(leaked)
    extra = dict(record)
    extra["filenames"] = [SECRET_NAME]
    assert incident.validate_record(extra)
    invented = dict(record)
    invented["sessions"] = {"state": "UNAVAILABLE", "reason": "SESSION_LIST_UNAVAILABLE", "persistent_count": 0}
    assert incident.validate_record(invented)


def main() -> int:
    test_packet_accepts_solo_operator_and_grants_nothing()
    test_packet_rejects_missing_unknown_and_contradictions()
    test_freeze_off_does_not_grant()
    test_historical_pressure_fixture_states_facts_without_root_cause()
    test_capture_is_schema_valid_real_git_and_redacted()
    test_missing_source_is_partial_without_fabricated_zero()
    test_session_file_seam_does_not_execute()
    test_agent_bin_sentinel_is_not_executed()
    test_fsmonitor_sentinel_is_not_executed()
    test_unavailable_agent_omits_count()
    test_retention_bounds_and_traversal_fail_closed()
    test_command_fields_cannot_execute()
    test_schema_rejects_unsafe_record_shapes()
    print("INCIDENT_EVIDENCE_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
