#!/usr/bin/env python3
"""Validate ChatGPT-executed user-acceptance evidence and same-HEAD quality closure."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "user-acceptance-evidence.schema.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ContractError(ValueError):
    pass


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label}_INVALID_ROOT")
    return value
def _validate_schema(data: dict[str, Any]) -> None:
    schema = _load_json(SCHEMA, "SCHEMA")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda item: list(item.path),
    )
    if errors:
        err = errors[0]
        path = ".".join(str(part) for part in err.path) or "<root>"
        raise ContractError(f"EVIDENCE_SCHEMA_INVALID:{path}:{err.message}")


def _gate_reasons(data: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    checks = (
        ("FINAL_STATUS_NOT_PASS", data.get("final_status") == "PASS"),
        ("CONTRACT_DIRTY", data.get("contract_dirty") is False),
        ("HEAD_CHANGED", data.get("head_unchanged") is True),
        ("CHATGPT_EXECUTOR_REQUIRED", data.get("executor") == "CHATGPT"),
        ("CHATGPT_FINAL_AUDITOR_REQUIRED", data.get("final_auditor") == "CHATGPT"),
        ("CHATGPT_DIRECT_PERSONA_EXECUTION_REQUIRED", data.get("chatgpt_direct_persona_execution") is True),
        ("ACTUAL_USER_SURFACE_MISSING", data.get("actual_user_surface") is True),
        ("SCRIPTED_USER_SUBSTITUTION", data.get("scripted_user_substitution") is False),
        ("FINDING_ACCUMULATION_INCOMPLETE", data.get("finding_accumulation_complete") is True),
        ("EVIDENCE_LEDGER_INVALID", data.get("evidence_ledger_schema") == "PASS"),
        ("SUMMARY_NOT_LEDGER_DERIVED", data.get("summary_derived_from_ledger") is True),
        ("REPORT_INCONSISTENT", data.get("report_consistency") == "PASS"),
    )
    reasons.extend(reason for reason, ok in checks if not ok)
    mandatory_total = int(data.get("mandatory_total") or 0)
    mandatory_pass = int(data.get("mandatory_pass") or 0)
    if mandatory_pass != mandatory_total:
        reasons.append("MANDATORY_COVERAGE_INCOMPLETE")
    for field in (
        "mandatory_fail", "mandatory_partial", "mandatory_blocked",
        "unresolved_blocking_findings",
    ):
        if int(data.get(field) or 0) != 0:
            reasons.append(field.upper() + "_NONZERO")

    gate = data.get("gate")
    if gate == "SURFACE_RECONCILIATION":
        if float(data.get("capability_coverage_pct") or 0) != 100:
            reasons.append("CAPABILITY_COVERAGE_INCOMPLETE")
        if float(data.get("public_surface_coverage_pct") or 0) != 100:
            reasons.append("PUBLIC_SURFACE_COVERAGE_INCOMPLETE")
    elif gate == "FULL_USER_E2E":
        if float(data.get("use_case_coverage_pct") or 0) != 100:
            reasons.append("USE_CASE_COVERAGE_INCOMPLETE")
        if float(data.get("real_effect_coverage_pct") or 0) != 100:
            reasons.append("REAL_EFFECT_COVERAGE_INCOMPLETE")
        if data.get("cleanup_status") != "PASS":
            reasons.append("CLEANUP_NOT_PASS")
    else:
        reasons.append("GATE_INVALID")
    return reasons
def validate_gate(path: Path, expected_gate: str | None = None) -> dict[str, Any]:
    data = _load_json(path, "EVIDENCE")
    _validate_schema(data)
    if expected_gate and data.get("gate") != expected_gate:
        raise ContractError(f"GATE_MISMATCH:{data.get('gate')}:{expected_gate}")
    reasons = _gate_reasons(data)
    if reasons:
        raise ContractError("GATE_BLOCK:" + ",".join(reasons))
    return data


def quality_close(surface_path: Path, e2e_path: Path, expected_head: str) -> None:
    if SHA_RE.fullmatch(expected_head) is None:
        raise ContractError("EXPECTED_HEAD_INVALID")
    surface = validate_gate(surface_path, "SURFACE_RECONCILIATION")
    e2e = validate_gate(e2e_path, "FULL_USER_E2E")
    if surface["candidate_head"] != e2e["candidate_head"]:
        raise ContractError("CANDIDATE_HEAD_MISMATCH")
    if surface["candidate_head"] != expected_head:
        raise ContractError("EXPECTED_HEAD_MISMATCH")
    print("PRODUCT_QUALITY_CLOSURE=PASS")
    print(f"CANDIDATE_FREEZE_ELIGIBLE_HEAD={expected_head}")
    print("USER_ACCEPTANCE_EXECUTOR=CHATGPT")
    print("AUTHORIZES_RELEASE=NO")
    print("PERFORMS_EXTERNAL_MUTATION=NO")
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    gate = sub.add_parser("validate-gate")
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument(
        "--expected-gate",
        choices=("SURFACE_RECONCILIATION", "FULL_USER_E2E"),
    )
    close = sub.add_parser("quality-close")
    close.add_argument("--surface-evidence", type=Path, required=True)
    close.add_argument("--e2e-evidence", type=Path, required=True)
    close.add_argument("--expected-head", required=True)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.cmd == "validate-gate":
            data = validate_gate(args.evidence, args.expected_gate)
            print("USER_ACCEPTANCE_GATE=PASS")
            print(f"GATE={data['gate']}")
            print(f"RUN_ID={data['run_id']}")
            print(f"CANDIDATE_HEAD={data['candidate_head']}")
        else:
            quality_close(
                args.surface_evidence,
                args.e2e_evidence,
                args.expected_head.lower(),
            )
        return 0
    except ContractError as exc:
        print(f"USER_ACCEPTANCE=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
