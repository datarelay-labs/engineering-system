#!/usr/bin/env python3
"""Deterministic regressions for the independent-verifier contract."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from independent_verifier import VerifierFactsError, evaluate  # noqa: E402

TOOL = ROOT / "tools" / "independent_verifier.py"
HEAD = "18c977b28a97ec18e435817cd752b6bc14e37fd2"
OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def base(
    *,
    risk: str = "HIGH",
    implementer_id: str = "impl-1",
    implementer_ctx: str = "ctx-impl",
    verifier_id: str = "ver-1",
    verifier_ctx: str = "ctx-ver",
    include_verifier: bool = True,
    oracle_result: str = "PASS",
    oracle_head: str = HEAD,
    findings: list | None = None,
    mutable: list | None = None,
    expected_mutable: dict | None = None,
    human_approval: dict | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "subject_head": HEAD,
        "change_risk": risk,
        "implementer": {"identity": implementer_id, "context_id": implementer_ctx},
        "oracle_evidence": [
            {"id": "ENG-ORACLE-001", "result": oracle_result, "subject_head": oracle_head}
        ],
        "review_findings": findings if findings is not None else [],
        "mutable_evidence": mutable
        if mutable is not None
        else (
            [
                {
                    "kind": "ci",
                    "subject_id": "pr-55",
                    "version_id": HEAD,
                    "result": "PASS",
                }
            ]
            if risk in {"HIGH", "CRITICAL"}
            else []
        ),
    }
    if risk in {"HIGH", "CRITICAL"} or expected_mutable is not None:
        payload["expected_mutable"] = (
            expected_mutable
            if expected_mutable is not None
            else {"ci": {"subject_id": "pr-55", "version_id": HEAD}}
        )
    if include_verifier:
        payload["verifier"] = {"identity": verifier_id, "context_id": verifier_ctx}
    if human_approval is not None:
        payload["human_approval"] = human_approval
    if extra:
        payload.update(extra)
    return payload


def run_cli(payload: dict) -> tuple[int, dict[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "request.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            ["python3", str(TOOL), "verify", "--request-json", str(path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    fields = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    return completed.returncode, fields


def test_high_pass_with_distinct_verifier() -> None:
    report = evaluate(base())
    assert report["DECISION"] == "PASS"
    assert report["EXECUTES_REQUEST_COMMANDS"] == "NO"
    assert report["VERIFIER_REQUIRED"] == "YES"


def test_high_denies_same_implementer_as_verifier() -> None:
    report = evaluate(
        base(verifier_id="impl-1", verifier_ctx="ctx-other")
    )
    assert report["DENY_CLASS"] == "SAME_ACTOR"
    report2 = evaluate(base(verifier_id="other", verifier_ctx="ctx-impl"))
    assert report2["DENY_CLASS"] == "SAME_ACTOR"


def test_high_denies_missing_verifier() -> None:
    report = evaluate(base(include_verifier=False))
    assert report["DENY_CLASS"] == "VERIFIER_REQUIRED"


def test_head_mismatch_and_oracle_not_pass() -> None:
    assert evaluate(base(oracle_head=OTHER))["DENY_CLASS"] == "HEAD_MISMATCH"
    assert evaluate(base(oracle_result="FAIL"))["DENY_CLASS"] == "ORACLE_NOT_PASS"
    assert evaluate(base(oracle_result="NOT_RUN"))["DENY_CLASS"] == "ORACLE_NOT_PASS"


def test_review_open_denied_fixed_allowed() -> None:
    open_finding = [
        {"id": "r1", "actionable": True, "disposition": "OPEN"},
    ]
    assert evaluate(base(findings=open_finding))["DENY_CLASS"] == "REVIEW_OPEN"
    fixed = [{"id": "r1", "actionable": True, "disposition": "FIXED"}]
    assert evaluate(base(findings=fixed))["DECISION"] == "PASS"
    dispositioned = [
        {"id": "r1", "actionable": True, "disposition": "EVIDENCE_DISPOSITION"}
    ]
    assert evaluate(base(findings=dispositioned))["DECISION"] == "PASS"


def test_mutable_requires_subject_version_identity() -> None:
    assert evaluate(base(mutable=[]))["DENY_CLASS"] == "MUTABLE_MISSING"
    try:
        evaluate(
            {
                **base(),
                "mutable_evidence": [
                    {"kind": "ci", "subject_id": "pr-1", "result": "PASS"}
                ],
            }
        )
        raise AssertionError("expected missing version_id to fail")
    except VerifierFactsError as exc:
        assert "version_id" in exc.reason


def test_low_medium_without_distinct_verifier() -> None:
    low = evaluate(base(risk="LOW", include_verifier=False, mutable=[]))
    assert low["DECISION"] == "PASS"
    assert low["VERIFIER_REQUIRED"] == "NO"
    medium = evaluate(
        base(
            risk="MEDIUM",
            include_verifier=False,
            mutable=[
                {
                    "kind": "review",
                    "subject_id": "pr-1",
                    "version_id": "review-rev-9",
                    "result": "PASS",
                }
            ],
            expected_mutable={
                "review": {"subject_id": "pr-1", "version_id": "review-rev-9"}
            },
        )
    )
    assert medium["DECISION"] == "PASS"


def test_p1b_verifier_001_stale_ci_denied() -> None:
    """Coordinator repro: HIGH with exact-head oracle but stale CI must DENY."""
    report = evaluate(
        base(
            mutable=[
                {
                    "kind": "ci",
                    "subject_id": "old-pr-17",
                    "version_id": "old-head-deadbeef",
                    "result": "PASS",
                }
            ],
            expected_mutable={"ci": {"subject_id": "pr-55", "version_id": HEAD}},
        )
    )
    assert report["DECISION"] == "DENY"
    assert report["DENY_CLASS"] == "STALE_MUTABLE"


def test_critical_requires_human_approval() -> None:
    assert evaluate(base(risk="CRITICAL"))["DENY_CLASS"] == "HUMAN_APPROVAL_MISSING"
    ok = evaluate(
        base(risk="CRITICAL", human_approval={"required": True, "present": True})
    )
    assert ok["DECISION"] == "PASS"


def test_forbids_execution_payload_keys() -> None:
    try:
        evaluate(base(extra={"command": "rm -rf /"}))
        raise AssertionError("expected forbidden command key")
    except VerifierFactsError as exc:
        assert exc.deny_class == "EXECUTION_FORBIDDEN"
    try:
        evaluate(base(extra={"shell": "echo hi"}))
        raise AssertionError("expected forbidden shell key")
    except VerifierFactsError as exc:
        assert exc.deny_class == "EXECUTION_FORBIDDEN"


def test_cli_pass_and_deny_and_no_subprocess_in_source() -> None:
    code, fields = run_cli(base())
    assert code == 0
    assert fields["DECISION"] == "PASS"
    code, fields = run_cli(base(verifier_id="impl-1"))
    assert code == 2
    assert fields["DENY_CLASS"] == "SAME_ACTOR"
    text = TOOL.read_text(encoding="utf-8")
    for token in ("import subprocess", "subprocess.", "os.system", "shell=True", "eval(", "exec("):
        assert token not in text, token


def main() -> int:
    test_high_pass_with_distinct_verifier()
    test_high_denies_same_implementer_as_verifier()
    test_high_denies_missing_verifier()
    test_head_mismatch_and_oracle_not_pass()
    test_review_open_denied_fixed_allowed()
    test_mutable_requires_subject_version_identity()
    test_low_medium_without_distinct_verifier()
    test_p1b_verifier_001_stale_ci_denied()
    test_critical_requires_human_approval()
    test_forbids_execution_payload_keys()
    test_cli_pass_and_deny_and_no_subprocess_in_source()
    print("INDEPENDENT_VERIFIER_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
