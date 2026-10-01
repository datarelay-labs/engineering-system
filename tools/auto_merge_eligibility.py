#!/usr/bin/env python3
"""Pure conditional auto-merge eligibility evaluator.

This module consumes bounded Trust Engineering and current PR/policy facts and
returns ELIGIBLE, DENY, or BLOCK. ELIGIBLE is a policy result only: every result
sets authorizes_merge=false and this tool has no network, shell, GitHub,
release, deployment, or external-write execution path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_REL = Path("schemas") / "auto-merge-eligibility.schema.json"


def _load_schema(root: Path) -> dict[str, Any]:
    return json.loads((root / SCHEMA_REL).read_text(encoding="utf-8"))


def _malformed_reason(root: Path, request: Any) -> str | None:
    try:
        schema = _load_schema(root)
    except (OSError, json.JSONDecodeError) as exc:
        return f"schema unavailable: {type(exc).__name__}"
    errors = sorted(
        Draft202012Validator(schema).iter_errors(request),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return None
    error = errors[0]
    where = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{where}: {error.message.splitlines()[0]}"


def _result(
    request: Any,
    decision: str,
    reason_class: str,
    reason: str,
) -> dict[str, Any]:
    subject = request.get("subject", {}) if isinstance(request, dict) else {}
    return {
        "schema_version": 1,
        "decision": decision,
        "reason_class": reason_class,
        "reason": reason,
        "eligible": decision == "ELIGIBLE",
        "target_repo": subject.get("target_repo", ""),
        "workstream": subject.get("workstream", ""),
        "intent_revision": subject.get("intent_revision", 0),
        "pr_number": subject.get("pr_number", 0),
        "subject_head": subject.get("head_sha", ""),
        "authorizes_merge": False,
        "external_mutation": False,
        "executes_commands": False,
        "performs_network_io": False,
    }


def evaluate(request: Any, root: Path = ROOT) -> dict[str, Any]:
    malformed = _malformed_reason(root, request)
    if malformed is not None:
        return _result(request, "BLOCK", "MALFORMED", malformed)

    subject = request["subject"]
    trust = request["trust"]
    policy = request["policy"]
    observed = request["observed"]
    head = subject["head_sha"]

    identity_fields = ("target_repo", "pr_number", "base_branch", "head_branch")
    for field in identity_fields:
        if observed[field] != subject[field]:
            return _result(
                request,
                "BLOCK",
                "STALE_IDENTITY",
                f"observed {field} does not match the eligibility subject",
            )
    if observed["head_sha"] != head:
        return _result(
            request,
            "BLOCK",
            "STALE_HEAD",
            "observed PR head does not match the eligibility subject",
        )

    trust_bindings = (
        ("target_repo", "target_repo"),
        ("workstream", "workstream"),
        ("intent_revision", "intent_revision"),
        ("subject_head", "head_sha"),
    )
    for trust_field, subject_field in trust_bindings:
        if trust[trust_field] != subject[subject_field]:
            return _result(
                request,
                "BLOCK",
                "STALE_TRUST",
                f"trust {trust_field} is not bound to the current subject",
            )

    mutation_state = observed["mutation_state"]
    if mutation_state == "AMBIGUOUS":
        return _result(request, "BLOCK", "MUTATION_AMBIGUOUS", "prior mutation outcome is ambiguous")
    if mutation_state == "IN_FLIGHT":
        return _result(request, "BLOCK", "MUTATION_IN_FLIGHT", "a mutation is still in flight")
    if mutation_state == "REPLAY":
        return _result(request, "BLOCK", "REPLAY", "the eligibility attempt is a replay")
    if mutation_state == "UNKNOWN":
        return _result(request, "BLOCK", "MUTATION_UNKNOWN", "mutation state is unknown")

    if trust["decision"] != "PASS":
        return _result(request, "BLOCK", "TRUST_BLOCKED", "Trust Engineering decision is not PASS")
    if trust["achieved"] != "T5":
        return _result(request, "BLOCK", "TRUST_BELOW_T5", "Trust Engineering level is below T5")
    if trust["automation_eligible"] != "YES":
        return _result(
            request,
            "BLOCK",
            "AUTOMATION_NOT_ELIGIBLE",
            "Trust Engineering does not mark this subject automation-eligible",
        )

    if subject["target_repo"] not in policy["allowed_repositories"]:
        return _result(request, "DENY", "REPOSITORY_NOT_ALLOWLISTED", "repository is not allowlisted")
    if subject["policy_profile"] not in policy["allowed_profiles"]:
        return _result(request, "DENY", "PROFILE_NOT_ALLOWED", "policy profile is not allowed")
    if subject["task_kind"] not in policy["allowed_task_kinds"]:
        return _result(request, "DENY", "TASK_KIND_NOT_ALLOWED", "task kind is not allowed")
    if subject["change_risk"] not in policy["allowed_change_risks"]:
        return _result(request, "DENY", "CHANGE_RISK_NOT_ALLOWED", "change risk is not allowed")
    if subject["base_branch"] not in policy["allowed_base_branches"]:
        return _result(request, "DENY", "BASE_BRANCH_NOT_ALLOWED", "base branch is not allowed")
    if not policy["ruleset_allows"]:
        return _result(request, "DENY", "RULESET_FORBIDS", "repository ruleset forbids eligibility")
    if not policy["auto_merge_enabled"]:
        return _result(request, "DENY", "AUTO_MERGE_DISABLED", "repository auto-merge is disabled")

    ci = observed["ci"]
    review = observed["review"]
    if ci["head_sha"] != head:
        return _result(request, "BLOCK", "STALE_CI", "CI evidence is bound to a different head")
    if review["head_sha"] != head:
        return _result(request, "BLOCK", "STALE_REVIEW", "review evidence is bound to a different head")
    if ci["status"] in {"PENDING", "UNKNOWN"}:
        return _result(request, "BLOCK", "CI_NOT_READY", "required CI is pending or unknown")
    if ci["status"] == "FAIL":
        return _result(request, "DENY", "CI_FAILED", "required CI is failing")
    if review["status"] in {"PENDING", "UNKNOWN"}:
        return _result(request, "BLOCK", "REVIEW_NOT_READY", "required review state is pending or unknown")
    if review["status"] == "FAIL":
        return _result(request, "DENY", "REVIEW_FAILED", "required review state is failing")
    if review["unresolved_threads"] != 0:
        return _result(request, "DENY", "UNRESOLVED_REVIEW_THREADS", "review threads remain unresolved")

    return _result(
        request,
        "ELIGIBLE",
        "ALL_GATES_PASS",
        "all exact-head trust, policy, CI, and review eligibility gates pass",
    )


def _read_request(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read request: {type(exc).__name__}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate",))
    parser.add_argument("--request-json", type=Path, required=True)
    args = parser.parse_args()

    try:
        request = _read_request(args.request_json)
    except ValueError as exc:
        report = _result({}, "BLOCK", "MALFORMED", str(exc))
        print(json.dumps(report, sort_keys=True))
        return 3

    report = evaluate(request)
    print(json.dumps(report, sort_keys=True))
    return 3 if report["reason_class"] == "MALFORMED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
