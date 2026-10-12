#!/usr/bin/env python3
"""Validate user-acceptance evidence structure against the current exact Git candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from execution_profile import load_profile_text, ProfileError, PROFILE_PATH
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "user-acceptance-evidence.schema.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_ORIGIN_PREFIXES = (
    "https://github.com/", "git@github.com:", "ssh://git@github.com/",
)


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


def _utc_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _committed_repository_origin(root: Path) -> str:
    """Bind owner-owned observations to the candidate's actual Git origin.

    Evidence supplied by the operator may contain internally consistent
    GitHub URLs from an unrelated repository. Those URLs cannot authenticate
    a run for the product currently under structural review. This check uses
    the local Git identity only; it does not grant trusted user provenance.
    """
    try:
        remote = str(_git(root, "remote", "get-url", "origin")).strip()
    except ContractError as exc:
        raise ContractError("HUMAN_OWNER_REPOSITORY_ORIGIN_UNAVAILABLE") from exc
    for prefix in GITHUB_ORIGIN_PREFIXES:
        if remote.startswith(prefix):
            repo = remote[len(prefix):].rstrip("/")
            if repo.endswith(".git"):
                repo = repo[:-4]
            if REPO_RE.fullmatch(repo) is not None:
                return repo.lower()
    raise ContractError("HUMAN_OWNER_REPOSITORY_ORIGIN_UNAVAILABLE")


def _human_owner_reasons(data: dict[str, Any], expected_repo: str) -> list[str]:
    """Structural review only: no submitted string can authenticate a human.

    Authenticated owner intent/physical actions and the ChatGPT evidence audit
    remain independent of this worker-writable evidence JSON. No verifier or
    signer from the unmerged alternate-runtime PR is imported or reused.
    """
    human = data.get("human_owner_evidence")
    if human is None:
        return ["HUMAN_OWNER_EVIDENCE_MISSING"]
    reasons: list[str] = []
    for outer, key in (
        ("candidate_head", "CANDIDATE_HEAD"),
        ("contract_sha256", "CONTRACT_SHA256"),
        ("run_id", "RUN_ID"),
    ):
        if human[outer] != data[outer]:
            reasons.append(f"HUMAN_OWNER_{key}_MISMATCH")
    dates = [_utc_timestamp(human[k]) for k in ("started_at", "completed_at")]
    if None in dates or dates[0] > dates[1]:
        reasons.append("HUMAN_OWNER_TIME_INCONSISTENT")
    client = human["client"]
    try:
        address = urlsplit(client["target"])
    except ValueError:
        return reasons + ["HUMAN_OWNER_SURFACE_TARGET_INVALID"]
    # Isolated local SSH port-forward is a legitimate real-browser surface;
    # remote browsers must use HTTPS. Query strings and userinfo can leak keys.
    kind = client["kind"]
    allowed = (
        (address.scheme == "https" and bool(address.hostname))
        or (address.scheme == "http" and address.hostname in {"localhost", "127.0.0.1", "::1"})
    ) if kind in {"CHROME", "CHROMIUM"} else bool(address.scheme)
    if (not allowed or address.username or address.password
            or address.query or address.fragment or any(ord(ch) < 32 for ch in client["target"])):
        reasons.append("HUMAN_OWNER_SURFACE_TARGET_INVALID")

    # Every user-observed scenario must be represented once, match the
    # mandatory result ledger and include a screenshot or actual read-back ref.
    missions = human["scenarios"]
    if len(missions) != data["mandatory_total"]:
        reasons.append("HUMAN_OWNER_SCENARIO_INCOMPLETE")
    ids: set[str] = set()
    owner_repo = urlsplit(human["owner_instruction_reference"]).path.split("/")[1:3]
    if "/".join(owner_repo).lower() != expected_repo:
        reasons.append("HUMAN_OWNER_REFERENCE_REPOSITORY_MISMATCH")
    references = [human[k] for k in (
        "owner_instruction_reference", "owner_observation_reference",
        "chatgpt_audit_reference",
    )] + [client["process_observation_reference"]]
    for item in missions:
        if item["id"] in ids:
            reasons.append("HUMAN_OWNER_SCENARIO_DUPLICATE")
        ids.add(item["id"])
        stamp = _utc_timestamp(item["executed_at"])
        if stamp is None or None in dates or not dates[0] <= stamp <= dates[1]:
            reasons.append("HUMAN_OWNER_SCENARIO_TIME_INVALID")
        if item["result"] != "PASS":
            reasons.append("HUMAN_OWNER_SCENARIO_NOT_PASS")
        if not item["screenshot_sha256"] and not item["readback_reference"]:
            reasons.append("HUMAN_OWNER_SCENARIO_OBSERVATION_MISSING")
        references.append(item["evidence_reference"])
        if item["readback_reference"] is not None:
            references.append(item["readback_reference"])
    if sum(item["result"] == "PASS" for item in missions) != data["mandatory_pass"]:
        reasons.append("HUMAN_OWNER_LEDGER_MISMATCH")
    for reference in references:
        if urlsplit(reference).path.split("/")[1:3] != owner_repo:
            reasons.append("HUMAN_OWNER_REFERENCE_REPOSITORY_MISMATCH")
            break
    if human["chatgpt_audit_claim"] != "PASS":
        reasons.append("HUMAN_OWNER_AUDIT_NOT_PASS")
    if human["operator_reported_cleanup"] != data["cleanup_status"]:
        reasons.append("HUMAN_OWNER_CLEANUP_MISMATCH")
    return list(dict.fromkeys(reasons))


def _structural_reasons(
    data: dict[str, Any], *, human_origin_repo: str | None = None
) -> list[str]:
    reasons: list[str] = []
    checks = (
        ("FINAL_STATUS_NOT_PASS", data.get("final_status") == "PASS"),
        ("HEAD_CHANGED", data.get("head_unchanged") is True),
        (
            "HUMAN_OWNER_EXECUTOR_REQUIRED"
            if data.get("user_execution_mode") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT"
            else "EXECUTOR_CLAIM_INVALID",
            data.get("executor") == (
                "HUMAN_OWNER" if data.get("user_execution_mode") ==
                "HUMAN_OWNER_WITH_CHATGPT_AUDIT" else "EXECUTION_PROFILE"
            ),
        ),
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
    if data.get("user_execution_mode", "AI_DIRECT") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT":
        if human_origin_repo is None:
            reasons.append("HUMAN_OWNER_REPOSITORY_ORIGIN_UNAVAILABLE")
        else:
            reasons.extend(_human_owner_reasons(data, human_origin_repo))
    elif "human_owner_evidence" in data:
        reasons.append("HUMAN_OWNER_EVIDENCE_UNEXPECTED")

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

    origin_repo = (
        _committed_repository_origin(root)
        if data.get("user_execution_mode") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT"
        else None
    )
    reasons = _structural_reasons(data, human_origin_repo=origin_repo)
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
    print(f"SURFACE_EXECUTOR_CLAIM={surface['executor']}")
    print(f"E2E_EXECUTOR_CLAIM={e2e['executor']}")
    print("EXECUTOR_PROVENANCE=UNVERIFIED")
    if (surface.get("user_execution_mode") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT"
            or e2e.get("user_execution_mode") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT"):
        print("HUMAN_OWNER_ATTESTATION=UNVERIFIED")
        print("CHATGPT_AUDIT_PROVENANCE=UNVERIFIED")
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
            if data.get("user_execution_mode") == "HUMAN_OWNER_WITH_CHATGPT_AUDIT":
                print("USER_EXECUTION_MODE=HUMAN_OWNER_WITH_CHATGPT_AUDIT")
                print("HUMAN_OWNER_ATTESTATION=UNVERIFIED")
                print("CHATGPT_AUDIT_PROVENANCE=UNVERIFIED")
            else:
                print("USER_EXECUTION_MODE=AI_DIRECT")
            print("USER_GATE_EXECUTION_PASS=NOT_ESTABLISHED")
        else:
            quality_close(args.surface_evidence, args.e2e_evidence, args.root)
        return 0
    except ContractError as exc:
        print(f"USER_ACCEPTANCE=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
