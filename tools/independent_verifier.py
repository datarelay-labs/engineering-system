#!/usr/bin/env python3
"""Deterministic provider-neutral independent verifier for terminal evidence.

Evaluates structured coordinator evidence only. Never executes repository
commands, shells, or other side effects from request payloads.

Exit status:
  0  PASS
  2  DENY for an explicit policy reason
  3  BLOCK because facts were missing, malformed, or ambiguous
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
ORACLE_PASS = "PASS"
ORACLE_FAIL_CLOSED = frozenset({"FAIL", "BLOCK", "BLOCKED", "NOT_RUN", "UNEXECUTED", ""})
REVIEW_OK = frozenset({"FIXED", "EVIDENCE_DISPOSITION", "NOT_ACTIONABLE"})
REVIEW_OPEN = frozenset({"OPEN", "UNRESOLVED", ""})
MUTABLE_KINDS = frozenset({"ci", "review", "runtime"})
FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "command",
        "commands",
        "shell",
        "argv",
        "execute",
        "exec",
        "script",
        "run",
        "subprocess",
        "bash",
        "powershell",
    }
)

REPORT_KEYS = (
    "DECISION",
    "EXIT_CODE",
    "REASON",
    "DENY_CLASS",
    "SUBJECT_HEAD",
    "CHANGE_RISK",
    "VERIFIER_REQUIRED",
    "EXECUTES_REQUEST_COMMANDS",
)


class VerifierFactsError(Exception):
    def __init__(self, reason: str, deny_class: str = "AMBIGUOUS_FACTS"):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VerifierFactsError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerifierFactsError(f"{label} must be a non-empty string")
    return value.strip()


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerifierFactsError(f"{label} must be a JSON array")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise VerifierFactsError(f"{label} must be a boolean")
    return value


def require_full_sha(value: Any, label: str) -> str:
    text = _require_str(value, label).lower()
    if not FULL_SHA_RE.fullmatch(text):
        raise VerifierFactsError(f"{label} must be a 40-char lowercase hex Git SHA")
    return text


def reject_execution_keys(payload: dict[str, Any], *, path: str = "request") -> None:
    for key in payload:
        lowered = str(key).strip().lower()
        if lowered in FORBIDDEN_REQUEST_KEYS:
            raise VerifierFactsError(
                f"{path} contains forbidden execution key {key!r}",
                "EXECUTION_FORBIDDEN",
            )
        child = payload[key]
        if isinstance(child, dict):
            reject_execution_keys(child, path=f"{path}.{key}")
        elif isinstance(child, list):
            for index, item in enumerate(child):
                if isinstance(item, dict):
                    reject_execution_keys(item, path=f"{path}.{key}[{index}]")


def parse_actor(raw: Any, label: str) -> dict[str, str]:
    data = _require_mapping(raw, label)
    return {
        "identity": _require_str(data.get("identity"), f"{label}.identity"),
        "context_id": _require_str(data.get("context_id"), f"{label}.context_id"),
    }


def parse_oracle(raw: Any, label: str) -> dict[str, str]:
    data = _require_mapping(raw, label)
    result = _require_str(data.get("result"), f"{label}.result").upper()
    return {
        "id": _require_str(data.get("id"), f"{label}.id"),
        "result": result,
        "subject_head": require_full_sha(data.get("subject_head"), f"{label}.subject_head"),
    }


def parse_review_finding(raw: Any, label: str) -> dict[str, Any]:
    data = _require_mapping(raw, label)
    disposition = _require_str(data.get("disposition"), f"{label}.disposition").upper()
    return {
        "id": _require_str(data.get("id"), f"{label}.id"),
        "actionable": _require_bool(data.get("actionable"), f"{label}.actionable"),
        "disposition": disposition,
    }


def parse_mutable(raw: Any, label: str) -> dict[str, str]:
    data = _require_mapping(raw, label)
    kind = _require_str(data.get("kind"), f"{label}.kind").lower()
    if kind not in MUTABLE_KINDS:
        raise VerifierFactsError(f"{label}.kind must be ci, review, or runtime")
    return {
        "kind": kind,
        "subject_id": _require_str(data.get("subject_id"), f"{label}.subject_id"),
        "version_id": _require_str(data.get("version_id"), f"{label}.version_id"),
        "result": _require_str(data.get("result"), f"{label}.result").upper(),
    }


def parse_expected_mutable(
    raw: Any, *, subject_head: str, change_risk: str
) -> dict[str, dict[str, str]] | None:
    """Parse expected mutable subject/version contract.

    For HIGH/CRITICAL, CI (and any declared review/runtime) expectations are
    required. CI version_id must equal the verification subject HEAD so stale
    unrelated CI identities cannot satisfy terminal PASS.
    """
    if raw is None:
        if change_risk in {"HIGH", "CRITICAL"}:
            raise VerifierFactsError(
                "HIGH/CRITICAL requires expected_mutable subject/version contract",
                "MUTABLE_CONTRACT_MISSING",
            )
        return None
    data = _require_mapping(raw, "expected_mutable")
    if not data:
        raise VerifierFactsError(
            "expected_mutable must declare at least one kind",
            "MUTABLE_CONTRACT_MISSING",
        )
    expected: dict[str, dict[str, str]] = {}
    for kind, value in data.items():
        kind_key = _require_str(kind, "expected_mutable kind").lower()
        if kind_key not in MUTABLE_KINDS:
            raise VerifierFactsError(
                f"expected_mutable contains unknown kind {kind_key}",
                "AMBIGUOUS_FACTS",
            )
        entry = _require_mapping(value, f"expected_mutable.{kind_key}")
        subject_id = _require_str(
            entry.get("subject_id"), f"expected_mutable.{kind_key}.subject_id"
        )
        version_id = _require_str(
            entry.get("version_id"), f"expected_mutable.{kind_key}.version_id"
        )
        if kind_key == "ci" and version_id != subject_head:
            # Source-qualification CI must bind to exact subject HEAD.
            raise VerifierFactsError(
                "expected_mutable.ci.version_id must equal subject_head",
                "MUTABLE_CONTRACT_MISSING",
            )
        expected[kind_key] = {"subject_id": subject_id, "version_id": version_id}
    if change_risk in {"HIGH", "CRITICAL"} and "ci" not in expected:
        raise VerifierFactsError(
            "HIGH/CRITICAL expected_mutable must include ci bound to subject_head",
            "MUTABLE_CONTRACT_MISSING",
        )
    return expected


def deny(reason: str, deny_class: str, **fields: Any) -> dict[str, str]:
    payload = {
        "DECISION": "DENY",
        "EXIT_CODE": "2",
        "REASON": reason,
        "DENY_CLASS": deny_class,
        "EXECUTES_REQUEST_COMMANDS": "NO",
    }
    for key, value in fields.items():
        payload[key] = str(value)
    return payload


def allow(reason: str, **fields: Any) -> dict[str, str]:
    payload = {
        "DECISION": "PASS",
        "EXIT_CODE": "0",
        "REASON": reason,
        "DENY_CLASS": "NONE",
        "EXECUTES_REQUEST_COMMANDS": "NO",
    }
    for key, value in fields.items():
        payload[key] = str(value)
    return payload


def evaluate(request: dict[str, Any]) -> dict[str, str]:
    reject_execution_keys(request)
    subject_head = require_full_sha(request.get("subject_head"), "subject_head")
    change_risk = _require_str(request.get("change_risk"), "change_risk").upper()
    if change_risk not in CHANGE_RISKS:
        raise VerifierFactsError("change_risk must be LOW, MEDIUM, HIGH, or CRITICAL")

    implementer = parse_actor(request.get("implementer"), "implementer")
    verifier_required = change_risk in {"HIGH", "CRITICAL"}
    verifier_raw = request.get("verifier")
    if verifier_required:
        if verifier_raw is None:
            return deny(
                "HIGH/CRITICAL requires an independent verifier actor",
                "VERIFIER_REQUIRED",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES",
            )
        verifier = parse_actor(verifier_raw, "verifier")
        if (
            verifier["identity"] == implementer["identity"]
            or verifier["context_id"] == implementer["context_id"]
        ):
            return deny(
                "implementer identity/context cannot satisfy independent verifier requirement",
                "SAME_ACTOR",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES",
            )
    elif verifier_raw is not None:
        # Optional for LOW/MEDIUM; if supplied, still parse for well-formedness.
        parse_actor(verifier_raw, "verifier")

    oracles_raw = _require_list(request.get("oracle_evidence"), "oracle_evidence")
    if not oracles_raw:
        return deny(
            "completion-oracle evidence is missing",
            "ORACLE_MISSING",
            SUBJECT_HEAD=subject_head,
            CHANGE_RISK=change_risk,
            VERIFIER_REQUIRED="YES" if verifier_required else "NO",
        )
    for index, item in enumerate(oracles_raw):
        oracle = parse_oracle(item, f"oracle_evidence[{index}]")
        if oracle["subject_head"] != subject_head:
            return deny(
                f"oracle {oracle['id']} subject_head does not match verification subject",
                "HEAD_MISMATCH",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES" if verifier_required else "NO",
            )
        if oracle["result"] != ORACLE_PASS:
            return deny(
                f"oracle {oracle['id']} result {oracle['result']} cannot satisfy terminal PASS",
                "ORACLE_NOT_PASS",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES" if verifier_required else "NO",
            )

    findings_raw = _require_list(request.get("review_findings", []), "review_findings")
    for index, item in enumerate(findings_raw):
        finding = parse_review_finding(item, f"review_findings[{index}]")
        if not finding["actionable"]:
            continue
        if finding["disposition"] in REVIEW_OPEN or finding["disposition"] not in REVIEW_OK:
            return deny(
                f"actionable review finding {finding['id']} is not resolved/dispositioned",
                "REVIEW_OPEN",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES" if verifier_required else "NO",
            )

    expected_mutable = parse_expected_mutable(
        request.get("expected_mutable"),
        subject_head=subject_head,
        change_risk=change_risk,
    )
    mutable_raw = _require_list(request.get("mutable_evidence", []), "mutable_evidence")
    # Mutable CI/review/runtime facts must carry subject/version identity whenever used.
    # HIGH/CRITICAL require at least one such current mutable evidence record.
    if change_risk in {"HIGH", "CRITICAL"} and not mutable_raw:
        return deny(
            f"{change_risk} requires mutable evidence with subject/version identity",
            "MUTABLE_MISSING",
            SUBJECT_HEAD=subject_head,
            CHANGE_RISK=change_risk,
            VERIFIER_REQUIRED="YES" if verifier_required else "NO",
        )
    if mutable_raw and expected_mutable is None and change_risk in {"MEDIUM", "HIGH", "CRITICAL"}:
        return deny(
            "mutable evidence requires expected_mutable subject/version contract",
            "MUTABLE_CONTRACT_MISSING",
            SUBJECT_HEAD=subject_head,
            CHANGE_RISK=change_risk,
            VERIFIER_REQUIRED="YES" if verifier_required else "NO",
        )
    for index, item in enumerate(mutable_raw):
        mutable = parse_mutable(item, f"mutable_evidence[{index}]")
        if mutable["result"] != ORACLE_PASS:
            return deny(
                f"mutable {mutable['kind']} evidence result {mutable['result']} is not PASS",
                "MUTABLE_NOT_PASS",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES" if verifier_required else "NO",
            )
        if expected_mutable is not None:
            expected = expected_mutable.get(mutable["kind"])
            if expected is None:
                return deny(
                    f"mutable {mutable['kind']} evidence has no expected subject/version contract",
                    "STALE_MUTABLE",
                    SUBJECT_HEAD=subject_head,
                    CHANGE_RISK=change_risk,
                    VERIFIER_REQUIRED="YES" if verifier_required else "NO",
                )
            if (
                mutable["subject_id"] != expected["subject_id"]
                or mutable["version_id"] != expected["version_id"]
            ):
                return deny(
                    f"mutable {mutable['kind']} evidence subject/version does not match expected contract",
                    "STALE_MUTABLE",
                    SUBJECT_HEAD=subject_head,
                    CHANGE_RISK=change_risk,
                    VERIFIER_REQUIRED="YES" if verifier_required else "NO",
                )

    if change_risk == "CRITICAL":
        approval = request.get("human_approval")
        if approval is None:
            return deny(
                "CRITICAL requires human_approval evidence",
                "HUMAN_APPROVAL_MISSING",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES",
            )
        approval_map = _require_mapping(approval, "human_approval")
        required = _require_bool(approval_map.get("required", True), "human_approval.required")
        present = _require_bool(approval_map.get("present"), "human_approval.present")
        if required and not present:
            return deny(
                "CRITICAL human approval is required but not present",
                "HUMAN_APPROVAL_MISSING",
                SUBJECT_HEAD=subject_head,
                CHANGE_RISK=change_risk,
                VERIFIER_REQUIRED="YES",
            )

    return allow(
        "terminal evidence satisfies independent-verifier contract",
        SUBJECT_HEAD=subject_head,
        CHANGE_RISK=change_risk,
        VERIFIER_REQUIRED="YES" if verifier_required else "NO",
    )


def format_report(fields: dict[str, str]) -> str:
    lines = []
    for key in REPORT_KEYS:
        if key not in fields:
            continue
        lines.append(f"{key}={fields[key].replace(chr(10), ' ').strip()}")
    for key, value in fields.items():
        if key in REPORT_KEYS:
            continue
        lines.append(f"{key}={value.replace(chr(10), ' ').strip()}")
    return "\n".join(lines) + "\n"


def failure_report(reason: str, deny_class: str = "AMBIGUOUS_FACTS") -> tuple[str, int]:
    return (
        format_report(
            {
                "DECISION": "DENY",
                "EXIT_CODE": "3",
                "REASON": reason,
                "DENY_CLASS": deny_class,
                "EXECUTES_REQUEST_COMMANDS": "NO",
            }
        ),
        3,
    )


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerifierFactsError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise VerifierFactsError(f"malformed JSON in {path}: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("verify",),
        help="Evaluate structured terminal evidence",
    )
    parser.add_argument(
        "--request-json",
        required=True,
        help="JSON file of structured verifier facts (no executable commands)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_json_file(Path(args.request_json))
        if not isinstance(payload, dict):
            raise VerifierFactsError("request JSON must be an object")
        if args.command != "verify":
            raise VerifierFactsError(f"unknown command {args.command}")
        report = evaluate(payload)
        sys.stdout.write(format_report(report))
        return int(report["EXIT_CODE"])
    except VerifierFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
        sys.stdout.write(text)
        return code


if __name__ == "__main__":
    # Fact evaluation only; never execute request-supplied commands.
    raise SystemExit(main())
