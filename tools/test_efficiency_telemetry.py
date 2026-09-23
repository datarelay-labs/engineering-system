#!/usr/bin/env python3
"""Regressions for local efficiency telemetry, privacy, and task budgets."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "efficiency_telemetry.py"

_SPEC = importlib.util.spec_from_file_location("efficiency_telemetry", TOOL)
assert _SPEC and _SPEC.loader
telemetry = importlib.util.module_from_spec(_SPEC)
sys.modules["efficiency_telemetry"] = telemetry
_SPEC.loader.exec_module(telemetry)

_BEHAVIOR_SPEC = importlib.util.spec_from_file_location(
    "behavior_eval_for_telemetry",
    ROOT / "tools" / "behavior_eval.py",
)
assert _BEHAVIOR_SPEC and _BEHAVIOR_SPEC.loader
behavior_eval = importlib.util.module_from_spec(_BEHAVIOR_SPEC)
sys.modules["behavior_eval_for_telemetry"] = behavior_eval
_BEHAVIOR_SPEC.loader.exec_module(behavior_eval)

HEAD = "a" * 40
STARTED = "2026-09-23T00:00:00Z"


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def _counts() -> dict[str, int]:
    return {field: 0 for field in telemetry.COUNT_FIELDS}


def _validation(outcome: str = "PASS") -> dict:
    return {
        "ids": ["ENG-TELEMETRY-001"],
        "exact_head": HEAD,
        "evidence_state": "EXACT_HEAD",
        "outcome": outcome,
    }


def _profile() -> dict[str, str | None]:
    return {"provider": "cursor", "model": "auto", "reasoning": "low", "toolset": "default"}


def _record(**overrides):
    budget = overrides.pop("budget", telemetry.TaskBudget(None, None))
    record = telemetry.build_record(
        repo=overrides.pop("repo", "datarelay-labs/engineering-system"),
        workstream=overrides.pop("workstream", "engineering-system-1-7-p0b"),
        task_kind=overrides.pop("task_kind", "DEVELOPMENT"),
        profile=overrides.pop("profile", _profile()),
        started_at=overrides.pop("started_at", STARTED),
        finished_at=overrides.pop("finished_at", STARTED),
        duration_seconds=overrides.pop("duration_seconds", 5),
        counts=overrides.pop("counts", _counts()),
        validation=overrides.pop("validation", _validation()),
        terminal=overrides.pop("terminal", "PASS"),
        budget=budget,
        usage=overrides.pop("usage", None),
        run_id=overrides.pop("run_id", "b" * 32),
    )
    if overrides:
        _fail(f"unused record overrides: {sorted(overrides)}")
    return record


def test_schema_rejects_freeform_and_prohibited_content() -> None:
    record = _record()
    for key, value, code in (
        ("prompt", "hidden prompt", "PROHIBITED_FIELD"),
        ("source", "print(1)", "PROHIBITED_FIELD"),
        ("tool_payload", {"cmd": "ls"}, "PROHIBITED_FIELD"),
        ("secret", "abc", "PROHIBITED_FIELD"),
        ("note", "freeform", "SCHEMA_INVALID"),
    ):
        leaked = json.loads(json.dumps(record))
        leaked[key] = value
        try:
            telemetry.parse_document(leaked)
        except telemetry.TelemetryError as exc:
            if exc.code != code:
                _fail(f"{key} returned {exc.code}")
        else:
            _fail(f"{key} was accepted")
    leaked = json.loads(json.dumps(record))
    leaked["profile"]["model"] = "sk-live-token"
    try:
        telemetry.parse_document(leaked)
    except telemetry.TelemetryError as exc:
        if exc.code != "PROHIBITED_SECRET":
            _fail(f"secret value returned {exc.code}")
    else:
        _fail("secret value was accepted")
    leaked = json.loads(json.dumps(record))
    leaked["profile"]["toolset"] = "/tmp/private"
    try:
        telemetry.parse_document(leaked)
    except telemetry.TelemetryError as exc:
        if exc.code != "LOCAL_PATH_PROHIBITED":
            _fail(f"path value returned {exc.code}")
    else:
        _fail("absolute path was accepted")


def test_missing_usage_stays_null() -> None:
    usage = telemetry.usage_from_exposed({"input_tokens": 12})
    if usage["input_tokens"] != 12:
        _fail("exposed input tokens were dropped")
    for key in ("output_tokens", "cache_read_tokens", "cache_write_tokens", "cost"):
        if usage[key] is not None:
            _fail(f"{key} was fabricated")
    record = _record(usage={"input_tokens": 12, "cost": None})
    if record["usage"]["cost"] is not None or record["usage"]["cache_read_tokens"] is not None:
        _fail("explicit null usage was inferred")
    try:
        telemetry.usage_from_exposed({"estimated_cost": 1})
    except telemetry.TelemetryError as exc:
        if exc.code != "USAGE_ESTIMATED":
            _fail(f"estimated usage returned {exc.code}")
    else:
        _fail("estimated usage was accepted")


def test_profile_switch_requires_justification() -> None:
    session = telemetry.SessionProfile(_profile())
    if session.current != session.start:
        _fail("session start profile drifted")
    try:
        session.assign("model", "other")
    except telemetry.TelemetryError as exc:
        if exc.code != "SILENT_PROFILE_SWITCH":
            _fail(f"silent switch returned {exc.code}")
    else:
        _fail("silent profile switch was accepted")
    if session.current["model"] != "auto":
        _fail("rejected switch mutated the profile")
    session.switch("model", "sonnet", "CORRECTNESS_REQUIRED")
    record = _record(profile=session)
    if record["profile"]["model"] != "sonnet":
        _fail("justified switch was not recorded")
    if record["profile_switches"] != [
        {
            "field": "model",
            "from": "auto",
            "to": "sonnet",
            "justification": "CORRECTNESS_REQUIRED",
        }
    ]:
        _fail("profile switch metadata drifted")
    try:
        session.switch("reasoning", "high", "")
    except telemetry.TelemetryError as exc:
        if exc.code != "PROFILE_SWITCH_UNJUSTIFIED":
            _fail(f"empty justification returned {exc.code}")
    else:
        _fail("empty justification was accepted")


def test_budget_exhaustion_yields_and_blocks_retries() -> None:
    budget = telemetry.TaskBudget(2, "turns")
    first = budget.consume(1)
    if first["disposition"] != "CONTINUE" or first["terminal"] is not None:
        _fail("budget consumed inside the soft limit yielded early")
    decision = budget.consume(1)
    if decision["state"] != "EXHAUSTED" or decision["disposition"] != "YIELD" or decision["terminal"] != "BLOCK":
        _fail(f"exhaustion decision drifted: {decision}")
    try:
        budget.consume(1)
    except telemetry.TelemetryError as exc:
        if exc.code != "RETRY_LOOP_BLOCKED":
            _fail(f"retry returned {exc.code}")
    else:
        _fail("retry after exhaustion was accepted")
    if budget.consumed != 2:
        _fail("blocked retry mutated consumption")
    open_budget = telemetry.TaskBudget(None, None)
    continued = open_budget.consume(5)
    if continued["state"] != "UNKNOWN" or continued["disposition"] != "CONTINUE" or open_budget.consumed is not None:
        _fail("missing soft limit fabricated a budget")
    record = _record(budget=budget, terminal="PASS")
    if record["terminal"] != "BLOCK" or record["budget"]["disposition"] != "YIELD":
        _fail("exhausted budget did not force BLOCK/YIELD")


def test_retention_is_local_bounded_and_gitignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _record()
        try:
            telemetry.write_record(root, record)
        except telemetry.TelemetryError as exc:
            if exc.code != "RETENTION_NOT_GITIGNORED":
                _fail(f"missing gitignore returned {exc.code}")
        else:
            _fail("record was written outside gitignore")
        (root / ".gitignore").write_text(".engineering/telemetry/\n", encoding="utf-8")
        destination = telemetry.write_record(root, record)
        body = destination.read_text(encoding="utf-8")
        if "prompt" in body or "tool_payload" in body or "/home/" in body:
            _fail("persisted record contained prohibited content")
        if not destination.is_relative_to(root / ".engineering" / "telemetry"):
            _fail("record escaped the local retention directory")
        original_max = telemetry.MAX_RECORD_BYTES
        telemetry.MAX_RECORD_BYTES = 10
        try:
            telemetry.write_record(root, _record(run_id="c" * 32))
        except telemetry.TelemetryError as exc:
            if exc.code != "RETENTION_UNBOUNDED":
                _fail(f"oversize record returned {exc.code}")
        else:
            _fail("oversize record was written")
        finally:
            telemetry.MAX_RECORD_BYTES = original_max
        for index in range(telemetry.MAX_RECORDS + 2):
            written = telemetry.write_record(root, _record(run_id=f"{index:032x}"))
            os.utime(written, (index + 1, index + 1))
        kept = list((root / ".engineering" / "telemetry").glob("*.json"))
        if len(kept) != telemetry.MAX_RECORDS:
            _fail(f"retention kept {len(kept)} records")
        (root / ".engineering" / "telemetry" / "DISABLED").write_text("1\n", encoding="utf-8")
        try:
            telemetry.write_record(root, _record(run_id="d" * 32))
        except telemetry.TelemetryError as exc:
            if exc.code != "TELEMETRY_DISABLED":
                _fail(f"disabled telemetry returned {exc.code}")
        else:
            _fail("disabled telemetry still wrote a record")


def test_report_keeps_exact_head_and_unknown_cost() -> None:
    passing = _record(usage={"input_tokens": 3})
    report = telemetry.build_report([passing], head=HEAD, evidence_state="EXACT_HEAD")
    if report["head"] != HEAD or report["evidence_state"] != "EXACT_HEAD" or report["terminal"] != "PASS":
        _fail("outcome report dropped exact-head PASS state")
    if report["cost"] is not None or report["validation_outcome"] != "PASS":
        _fail("unknown cost was fabricated in the report")
    blocked = _record(run_id="e" * 32, terminal="BLOCK", validation=_validation("BLOCK"))
    mixed = telemetry.build_report([passing, blocked], head=HEAD, evidence_state="EXACT_HEAD")
    if mixed["terminal"] != "BLOCK" or mixed["validation_outcome"] != "BLOCK":
        _fail("BLOCK outcome was not reported")
    try:
        telemetry.build_report([passing], head="b" * 40, evidence_state="EXACT_HEAD")
    except telemetry.TelemetryError as exc:
        if exc.code != "EXACT_HEAD_MISMATCH":
            _fail(f"head mismatch returned {exc.code}")
    else:
        _fail("mismatched exact head was reported as EXACT_HEAD")


def test_behavior_rollout_gate_includes_efficiency_contract() -> None:
    source = inspect.getsource(behavior_eval.evaluate_gate)
    if "efficiency_contract_reasons" not in source:
        _fail("behavior rollout gate does not consult the efficiency contract")
    if behavior_eval.efficiency_contract_reasons(ROOT):
        _fail(f"canonical contract regressed: {behavior_eval.efficiency_contract_reasons(ROOT)}")
    with tempfile.TemporaryDirectory() as tmp:
        reasons = behavior_eval.efficiency_contract_reasons(Path(tmp))
        if "EFFICIENCY_CONTRACT_REGRESSION" not in reasons or "RETENTION_NOT_IGNORED" not in reasons:
            _fail(f"incomplete repository did not fail closed: {reasons}")
    catalog = behavior_eval.load_catalog()
    document = {
        "schema_version": 1,
        "kind": "behavior-eval-run",
        "runner": "deterministic",
        "head": HEAD,
        "provider": None,
        "model": None,
        "scenarios": [
            {
                "id": item["id"],
                "mandatory": True,
                "safety": True,
                "status": "PASS",
                "evidence": "CONTEXT_ROUTING_OK",
            }
            for item in catalog
        ],
    }
    gate = behavior_eval.evaluate_gate(document, expected_head=HEAD, baseline_status="PASS")
    if gate["status"] != "PASS" or gate["reasons"]:
        _fail(f"healthy efficiency contract blocked behavior rollout: {gate['reasons']}")


def test_gate_command_passes_offline() -> None:
    completed = subprocess.run(
        ["python3", str(TOOL), "gate"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        _fail(f"gate command failed: {completed.stdout} {completed.stderr}")
    payload = json.loads(completed.stdout)
    if payload["status"] != "PASS" or payload["reasons"]:
        _fail(f"gate payload drifted: {payload}")
    if "http://" in TOOL.read_text(encoding="utf-8") or "urllib" in TOOL.read_text(encoding="utf-8"):
        _fail("telemetry tool gained a network export path")


def main() -> None:
    test_schema_rejects_freeform_and_prohibited_content()
    test_missing_usage_stays_null()
    test_profile_switch_requires_justification()
    test_budget_exhaustion_yields_and_blocks_retries()
    test_retention_is_local_bounded_and_gitignored()
    test_report_keeps_exact_head_and_unknown_cost()
    test_behavior_rollout_gate_includes_efficiency_contract()
    test_gate_command_passes_offline()
    print("PASS efficiency telemetry")


if __name__ == "__main__":
    main()
