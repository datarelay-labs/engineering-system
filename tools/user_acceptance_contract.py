#!/usr/bin/env python3
"""Validate user-acceptance evidence structure against the current exact Git candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from execution_profile import load_profile_text, ProfileError, PROFILE_PATH
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


def _git(root: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=text,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ContractError("GIT_STATE_UNAVAILABLE") from exc


def _current_head(root: Path) -> str:
    head = str(_git(root, "rev-parse", "HEAD")).strip().lower()
    if SHA_RE.fullmatch(head) is None:
        raise ContractError("CURRENT_HEAD_INVALID")
    return head


def _repository_relative(value: object) -> str:
    raw = str(value or "").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ContractError("CONTRACT_PATH_INVALID")
    return path.as_posix()


def _committed_contract(root: Path, head: str, rel: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "show", f"{head}:{rel}"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ContractError("CONTRACT_NOT_TRACKED_AT_CANDIDATE") from exc


def _contract_dirty(root: Path, rel: str, committed: bytes) -> bool:
    path = root / rel
    try:
        working = path.read_bytes()
    except OSError:
        return True
    if working != committed:
        return True
    status = str(_git(root, "status", "--porcelain=v1", "--", rel)).strip()
    return bool(status)


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


def _structural_reasons(data: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    checks = (
        ("FINAL_STATUS_NOT_PASS", data.get("final_status") == "PASS"),
        ("HEAD_CHANGED", data.get("head_unchanged") is True),
        ("EXECUTOR_CLAIM_INVALID", data.get("executor") == "EXECUTION_PROFILE"),
        ("FINAL_AUDITOR_CLAIM_INVALID", data.get("final_auditor") == "EXECUTION_PROFILE"),
        ("PERSONA_EXECUTION_CLAIM_INVALID", data.get("direct_persona_execution") is True),
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
        "mandatory_fail",
        "mandatory_partial",
        "mandatory_blocked",
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


def validate_gate(
    path: Path,
    root: Path,
    expected_gate: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    data = _load_json(path, "EVIDENCE")
    _validate_schema(data)

    if expected_gate and data.get("gate") != expected_gate:
        raise ContractError(f"GATE_MISMATCH:{data.get('gate')}:{expected_gate}")

    current_head = _current_head(root)
    evidence_head = str(data.get("candidate_head") or "").lower()
    if evidence_head != current_head:
        raise ContractError(f"CANDIDATE_HEAD_NOT_CURRENT:{evidence_head}:{current_head}")

    rel = _repository_relative(data.get("contract_path"))
    committed = _committed_contract(root, current_head, rel)
    digest = hashlib.sha256(committed).hexdigest()
    if str(data.get("contract_sha256") or "").lower() != digest:
        raise ContractError("CONTRACT_SHA256_MISMATCH")
    actual_dirty = _contract_dirty(root, rel, committed)
    if bool(data.get("contract_dirty")) != actual_dirty:
        raise ContractError("CONTRACT_DIRTY_CLAIM_MISMATCH")
    if actual_dirty:
        raise ContractError("CONTRACT_DIRTY")

    reasons = _structural_reasons(data)
    try:
        profile_text = str(_git(root, "show", f"{current_head}:{PROFILE_PATH}"))
        selected_runtime = str(load_profile_text(profile_text)["runtime"]["primary"])
    except ProfileError as exc:
        raise ContractError(f"EXECUTION_PROFILE_UNAVAILABLE:{exc}") from exc
    if data.get("runtime") != selected_runtime:
        reasons.append("EXECUTION_PROFILE_RUNTIME_MISMATCH")
    if reasons:
        raise ContractError("GATE_STRUCTURAL_BLOCK:" + ",".join(reasons))
    return data


def quality_close(surface_path: Path, e2e_path: Path, root: Path) -> None:
    root = root.resolve()
    current_head = _current_head(root)
    surface = validate_gate(surface_path, root, "SURFACE_RECONCILIATION")
    e2e = validate_gate(e2e_path, root, "FULL_USER_E2E")
    if surface["candidate_head"] != e2e["candidate_head"]:
        raise ContractError("CANDIDATE_HEAD_MISMATCH")
    print("PRODUCT_QUALITY_CLOSURE_STRUCTURAL=PASS")
    print(f"STRUCTURALLY_READY_HEAD={current_head}")
    print("EXECUTOR_CLAIM=EXECUTION_PROFILE")
    print("EXECUTOR_PROVENANCE=UNVERIFIED")
    print("TRUSTED_PERSONA_ATTESTATION=REQUIRED")
    print("PRODUCT_QUALITY_CLOSURE=BLOCK")
    print("CANDIDATE_FREEZE_ELIGIBLE=NO")
    print("AUTHORIZES_RELEASE=NO")
    print("PERFORMS_EXTERNAL_MUTATION=NO")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    gate = sub.add_parser("validate-gate")
    gate.add_argument("--root", type=Path, required=True)
    gate.add_argument("--evidence", type=Path, required=True)
    gate.add_argument(
        "--expected-gate",
        choices=("SURFACE_RECONCILIATION", "FULL_USER_E2E"),
    )

    close = sub.add_parser("quality-close")
    close.add_argument("--root", type=Path, required=True)
    close.add_argument("--surface-evidence", type=Path, required=True)
    close.add_argument("--e2e-evidence", type=Path, required=True)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.cmd == "validate-gate":
            data = validate_gate(args.evidence, args.root, args.expected_gate)
            print("USER_ACCEPTANCE_GATE_STRUCTURAL=PASS")
            print(f"GATE={data['gate']}")
            print(f"RUN_ID={data['run_id']}")
            print(f"CANDIDATE_HEAD={data['candidate_head']}")
            print("EXECUTOR_PROVENANCE=UNVERIFIED")
            print("USER_GATE_EXECUTION_PASS=NOT_ESTABLISHED")
        else:
            quality_close(args.surface_evidence, args.e2e_evidence, args.root)
        return 0
    except ContractError as exc:
        print(f"USER_ACCEPTANCE=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
