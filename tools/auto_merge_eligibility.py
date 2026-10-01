#!/usr/bin/env python3
"""Pure conditional auto-merge eligibility evaluator.

Positive eligibility reuses the #67 verification contract. Caller JSON cannot
mint T5 authority: the public CLI has no boundary input and therefore cannot
produce ELIGIBLE. In-process callers must supply the exact
TrustedCoordinatorBoundary type consumed by verification-contract.py, whose
assessor must independently return PASS/T5/AUTOMATION_ELIGIBLE=YES for the
same repository, workstream, intent revision, and HEAD.

ELIGIBLE remains evidence-only. Every result fixes merge/external mutation,
command execution, and network I/O authority to false.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
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
    candidate = request.get("subject") if isinstance(request, dict) else None
    subject = candidate if isinstance(candidate, dict) else {}

    def string_field(key: str) -> str:
        value = subject.get(key)
        return value if isinstance(value, str) else ""

    def integer_field(key: str) -> int:
        value = subject.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    return {
        "schema_version": 1,
        "decision": decision,
        "reason_class": reason_class,
        "reason": reason,
        "eligible": decision == "ELIGIBLE",
        "target_repo": string_field("target_repo"),
        "workstream": string_field("workstream"),
        "intent_revision": integer_field("intent_revision"),
        "pr_number": integer_field("pr_number"),
        "subject_head": string_field("head_sha"),
        "authorizes_merge": False,
        "external_mutation": False,
        "executes_commands": False,
        "performs_network_io": False,
    }


def verification_module() -> Any:
    name = "verification_contract"
    cached = sys.modules.get(name)
    if cached is not None and hasattr(cached, "TrustedCoordinatorBoundary"):
        return cached
    path = Path(__file__).resolve().parent / "verification-contract.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("verification contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _trusted_t5_reason(
    root: Path,
    request: dict[str, Any],
    boundary: Any,
) -> tuple[str, str] | None:
    try:
        module = verification_module()
    except (OSError, RuntimeError):
        return ("TRUST_BOUNDARY_UNAVAILABLE", "verification contract boundary is unavailable")
    if type(boundary) is not module.TrustedCoordinatorBoundary:
        return (
            "UNTRUSTED_BOUNDARY",
            "T5 eligibility requires an in-process TrustedCoordinatorBoundary",
        )

    subject = request["subject"]
    expected = {
        "target_repo": subject["target_repo"],
        "workstream": subject["workstream"],
        "intent_revision": subject["intent_revision"],
        "subject_head": subject["head_sha"],
    }
    verification = request["verification"]
    report = module.assess(
        root,
        verification["receipt"],
        expected,
        verification["manifest"],
        boundary,
    )
    if report.get("DECISION") != "PASS":
        deny_class = str(report.get("DENY_CLASS") or "TRUST_BLOCKED")
        reason = str(report.get("REASON") or "Trust Engineering assessment did not pass")
        return (deny_class, reason)
    if report.get("ACHIEVED") != "T5":
        return ("TRUST_BELOW_T5", "Trust Engineering assessment is below T5")
    if report.get("AUTOMATION_ELIGIBLE") != "YES":
        return (
            "AUTOMATION_NOT_ELIGIBLE",
            "Trust Engineering assessment is not automation-eligible",
        )
    if report.get("EXTERNAL_MUTATION") != "NO":
        return (
            "TRUST_MUTATION_INVALID",
            "Trust Engineering assessment unexpectedly carries mutation authority",
        )
    return None


def evaluate(
    request: Any,
    root: Path = ROOT,
    boundary: Any = None,
) -> dict[str, Any]:
    malformed = _malformed_reason(root, request)
    if malformed is not None:
        return _result(request, "BLOCK", "MALFORMED", malformed)

    subject = request["subject"]
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

    mutation_state = observed["mutation_state"]
    if mutation_state == "AMBIGUOUS":
        return _result(request, "BLOCK", "MUTATION_AMBIGUOUS", "prior mutation outcome is ambiguous")
    if mutation_state == "IN_FLIGHT":
        return _result(request, "BLOCK", "MUTATION_IN_FLIGHT", "a mutation is still in flight")
    if mutation_state == "REPLAY":
        return _result(request, "BLOCK", "REPLAY", "the eligibility attempt is a replay")
    if mutation_state == "UNKNOWN":
        return _result(request, "BLOCK", "MUTATION_UNKNOWN", "mutation state is unknown")

    trust_problem = _trusted_t5_reason(root, request, boundary)
    if trust_problem is not None:
        reason_class, reason = trust_problem
        return _result(request, "BLOCK", reason_class, reason)

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

    # The public CLI deliberately exposes no boundary option. It can validate
    # and deny caller facts but can never produce ELIGIBLE.
    report = evaluate(request)
    print(json.dumps(report, sort_keys=True))
    return 3 if report["reason_class"] == "MALFORMED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
