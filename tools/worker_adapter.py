#!/usr/bin/env python3
"""Trusted worker/external-write gate for one coordinator action.

Compares a bound request with authoritative Work Packet facts and classifies
whether exactly one typed external effect may proceed. This process does not
perform the GitHub mutation, mint dispatch authority, spawn a shell, or stop
sessions. Callers must re-read durable state immediately before a write and
abort unless the result is APPLIED.

Command:
  evaluate   Classify one bound external-write request

Exit status:
  0  a result class was emitted
  3  facts were missing or malformed
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from work_packet_authority import authorize_work_packet_author_permission


def _load_skills_contract():
    path = Path(__file__).resolve().parent / "skills-contract.py"
    spec = importlib.util.spec_from_file_location("skills_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("skills contract helper is unavailable")
    module = sys.modules.get("skills_contract")
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules["skills_contract"] = module
        spec.loader.exec_module(module)
    return module


_SKILLS = _load_skills_contract()
authorize = _SKILLS.authorize
canonical_request_sha256 = _SKILLS.canonical_request_sha256
consume_dispatch_once = _SKILLS.consume_dispatch_once
finalize_dispatch = _SKILLS.finalize_dispatch
reserve_dispatch = _SKILLS.reserve_dispatch
resolve_trust_anchor = _SKILLS.resolve_trust_anchor
verify_signed_json = _SKILLS.verify_signed_json

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESULTS = frozenset(
    {
        "APPLIED",
        "NO_CHANGE",
        "STALE_WORKER",
        "AUTHORITY_DENIED",
        "RESOURCE_BLOCKED",
        "RECONCILE_AMBIGUOUS",
        "TRANSIENT_RETRYABLE",
        "FAILED_SEMANTIC",
    }
)
ALLOWED_ACTIONS = frozenset(
    {
        "update_work_packet",
        "comment_work_packet",
        "create_pull_request",
        "update_pull_request",
        "authorize_publication",
    }
)
PACKET_STATUSES = frozenset({"ACTIVE", "PAUSED", "BLOCKED", "COMPLETE"})
AMBIGUOUS_OUTCOMES = frozenset({"TIMEOUT", "UNKNOWN", "AMBIGUOUS"})
MUTATION_OUTCOMES = AMBIGUOUS_OUTCOMES | {"NOT_SENT", "APPLIED"}
FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "command",
        "commands",
        "shell",
        "argv",
        "execute",
        "exec",
        "script",
        "subprocess",
        "bash",
        "powershell",
        "endpoint",
        "signing_key",
        "hmac_secret",
        "private_key",
        "mint",
    }
)
TOP_LEVEL_KEYS = frozenset(
    {
        "bound_request",
        "authoritative",
        "author_permission",
        "author_association",
        "trusted_dispatch",
        "resource",
        "admission",
        "prior_delivery",
        "mutation",
        "failure",
        "wait",
        "proposed_mutation",
        "verification",
    }
)

DISPATCH_TOOL_ID = "network.post"
REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_KEYS = (
    "RESULT",
    "EXIT_CODE",
    "REASON",
    "AUTHORIZES_WRITE",
    "WRITES",
    "ISSUE_MUTATED",
    "REQUEST_DIGEST",
    "STOPS_UNRELATED_SESSIONS",
    "SPAWNS_PROCESS",
    "MINTS_AUTHORITY",
    "SENDS_NOTIFICATION",
)


class AdapterFactsError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdapterFactsError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterFactsError(f"{label} must be a non-empty string")
    return value.strip()


def _require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterFactsError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise AdapterFactsError(f"{label} must be >= {minimum}")
    return value


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AdapterFactsError(f"{label} contains unknown key {unknown[0]!r}")


def _contains_forbidden_key(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip().lower() in FORBIDDEN_REQUEST_KEYS:
                return str(key)
            found = _contains_forbidden_key(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _contains_forbidden_key(item)
            if found:
                return found
    return None


def concrete_effect(bound: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Exact external effect covered by the trusted dispatch hash."""
    return {
        "branch": bound["branch"],
        "expected_status": bound["expected_status"],
        "intent_revision": bound["intent_revision"],
        "mutation_content": proposed["content"],
        "mutation_target": proposed["target"],
        "requested_action": bound["requested_action"],
        "subject_head": bound["subject_head"],
        "target_repo": bound["target_repo"],
        "workstream": bound["workstream"],
    }


def request_digest(bound: dict[str, Any], proposed: dict[str, Any]) -> str:
    return canonical_request_sha256(concrete_effect(bound, proposed))


def _sha(value: Any, label: str) -> str:
    text = _require_str(value, label).lower()
    if not FULL_SHA_RE.fullmatch(text):
        raise AdapterFactsError(f"{label} must be a 40-char lowercase hex Git SHA")
    return text


def _parse_bound(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "bound_request")
    _reject_unknown(
        data,
        frozenset(
            {
                "target_repo",
                "workstream",
                "intent_revision",
                "subject_head",
                "requested_action",
                "branch",
                "expected_status",
            }
        ),
        "bound_request",
    )
    repository = _require_str(data.get("target_repo"), "bound_request.target_repo")
    if not REPOSITORY_RE.fullmatch(repository):
        raise AdapterFactsError("bound_request.target_repo must be owner/name")
    workstream = _require_str(data.get("workstream"), "bound_request.workstream")
    if not WORKSTREAM_RE.fullmatch(workstream):
        raise AdapterFactsError("bound_request.workstream must be a stable slug")
    action = _require_str(data.get("requested_action"), "bound_request.requested_action")
    status = _require_str(data.get("expected_status", "ACTIVE"), "bound_request.expected_status").upper()
    if status not in PACKET_STATUSES:
        raise AdapterFactsError(f"bound_request.expected_status is unknown: {status}")
    return {
        "target_repo": repository,
        "workstream": workstream,
        "intent_revision": _require_int(data.get("intent_revision"), "bound_request.intent_revision", minimum=1),
        "subject_head": _sha(data.get("subject_head"), "bound_request.subject_head"),
        "requested_action": action,
        "branch": _require_str(data.get("branch"), "bound_request.branch"),
        "expected_status": status,
    }


def _parse_authoritative(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "authoritative")
    _reject_unknown(
        data,
        frozenset({"target_repo", "workstream", "intent_revision", "status", "subject_head", "branch"}),
        "authoritative",
    )
    status = _require_str(data.get("status"), "authoritative.status").upper()
    if status not in PACKET_STATUSES:
        raise AdapterFactsError(f"authoritative.status is unknown: {status}")
    repository = _require_str(data.get("target_repo"), "authoritative.target_repo")
    return {
        "target_repo": repository,
        "workstream": _require_str(data.get("workstream"), "authoritative.workstream"),
        "intent_revision": _require_int(data.get("intent_revision"), "authoritative.intent_revision", minimum=1),
        "status": status,
        "subject_head": _sha(data.get("subject_head"), "authoritative.subject_head"),
        "branch": _require_str(data.get("branch"), "authoritative.branch"),
    }


def _parse_proposed(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "proposed_mutation")
    _reject_unknown(data, frozenset({"target", "content"}), "proposed_mutation")
    target = _require_mapping(data.get("target"), "proposed_mutation.target")
    _reject_unknown(target, frozenset({"kind", "id"}), "proposed_mutation.target")
    kind = _require_str(target.get("kind"), "proposed_mutation.target.kind")
    if kind not in {"issue", "pull_request"}:
        raise AdapterFactsError(f"proposed_mutation.target.kind is unknown: {kind}")
    if "content" not in data or not isinstance(data.get("content"), str):
        raise AdapterFactsError("proposed_mutation.content must be a string")
    return {
        "target": {"kind": kind, "id": _require_str(target.get("id"), "proposed_mutation.target.id")},
        "content": data["content"],
    }


def _parse_verification(raw: Any) -> dict[str, Path] | None:
    if raw is None:
        return None
    data = _require_mapping(raw, "verification")
    _reject_unknown(data, frozenset({"binding_assertion", "dispatch_assertion"}), "verification")
    return {
        "binding_assertion": Path(_require_str(data.get("binding_assertion"), "verification.binding_assertion")),
        "dispatch_assertion": Path(_require_str(data.get("dispatch_assertion"), "verification.dispatch_assertion")),
    }


def _authorize_concrete_effect(
    verification: dict[str, Path] | None,
    effect: dict[str, Any],
    *,
    consume_replay: bool = True,
) -> str:
    """Return ALLOW, REPLAY, or a denial reason. Caller JSON cannot grant this."""
    if verification is None:
        return "trusted verification assertions are missing; caller-supplied permission or dispatch facts are not authority"
    anchor = resolve_trust_anchor()
    if anchor is None:
        return "trusted verification boundary is unavailable"
    binding = verification["binding_assertion"]
    dispatch = verification["dispatch_assertion"]
    try:
        dispatch_payload = verify_signed_json(dispatch, anchor)
        binding_payload = verify_signed_json(binding, anchor)
    except (OSError, json.JSONDecodeError, SystemExit):
        return "trusted dispatch signature verification failed"
    classes = dispatch_payload.get("classes")
    if dispatch_payload.get("tool_id") != DISPATCH_TOOL_ID or not isinstance(classes, list) or "external_write" not in classes:
        return "dispatch is not a signed network.post external_write for this effect"
    if dispatch_payload.get("request_sha256") != canonical_request_sha256(effect):
        return "dispatch is not bound to the concrete mutation target and content"
    try:
        authorize_work_packet_author_permission(binding_payload.get("authority_permission"))
    except SystemExit:
        return "signed binding permission is missing or weaker than write"
    try:
        decision = authorize(
            REPO_ROOT,
            binding_assertion=binding,
            dispatch_assertion=dispatch,
            request_json=json.dumps(effect),
            consume_replay=consume_replay,
        )
    except SystemExit:
        return "trusted authorize path failed closed"
    if decision.allowed:
        return "ALLOW"
    if decision.reason == "REPLAY":
        return "REPLAY"
    return f"trusted authorize path denied the effect: {decision.reason}"


def _parse_prior(raw: Any) -> str | None:
    if raw is None:
        return None
    data = _require_mapping(raw, "prior_delivery")
    _reject_unknown(data, frozenset({"applied_digest"}), "prior_delivery")
    if "applied_digest" not in data or data.get("applied_digest") is None:
        return None
    return _require_str(data.get("applied_digest"), "prior_delivery.applied_digest").lower()


def _parse_mutation(raw: Any) -> str:
    if raw is None:
        return "NOT_SENT"
    data = _require_mapping(raw, "mutation")
    _reject_unknown(data, frozenset({"outcome"}), "mutation")
    outcome = _require_str(data.get("outcome", "NOT_SENT"), "mutation.outcome").upper()
    if outcome not in MUTATION_OUTCOMES:
        raise AdapterFactsError(f"mutation.outcome is unknown: {outcome}")
    return outcome


def _parse_failure(raw: Any) -> str:
    if raw is None:
        return "NONE"
    data = _require_mapping(raw, "failure")
    _reject_unknown(data, frozenset({"class"}), "failure")
    return _require_str(data.get("class", "NONE"), "failure.class").upper()


def _parse_wait(raw: Any) -> tuple[int, int]:
    if raw is None:
        return 0, 3
    data = _require_mapping(raw, "wait")
    _reject_unknown(data, frozenset({"retry_count", "retry_budget"}), "wait")
    count = _require_int(data.get("retry_count", 0), "wait.retry_count", minimum=0)
    budget = _require_int(data.get("retry_budget", 3), "wait.retry_budget", minimum=0)
    return count, budget


def _emit(result: str, reason: str, digest: str, *, authorize_write: bool) -> dict[str, Any]:
    if result not in RESULTS:
        raise AdapterFactsError(f"internal result is unknown: {result}")
    return {
        "schema_version": 1,
        "result": result,
        "reason": reason,
        "authorizes_write": authorize_write,
        "writes": 1 if authorize_write else 0,
        "issue_mutated": False,
        "request_digest": digest,
        "stops_unrelated_sessions": False,
        "spawns_process": False,
        "mints_authority": False,
        "sends_notification": False,
    }


def evaluate(payload: dict[str, Any], *, consume_replay: bool = True) -> dict[str, Any]:
    """Classify one external-write request. The same facts always match."""
    data = _require_mapping(payload, "request")
    forbidden = _contains_forbidden_key(data)
    if forbidden:
        return _emit(
            "AUTHORITY_DENIED",
            f"request contains forbidden execution or authority-mint key {forbidden}",
            "NONE",
            authorize_write=False,
        )
    _reject_unknown(data, TOP_LEVEL_KEYS, "request")
    if "bound_request" not in data or "authoritative" not in data:
        raise AdapterFactsError("bound_request and authoritative facts are required")
    bound = _parse_bound(data["bound_request"])
    authoritative = _parse_authoritative(data["authoritative"])
    if "proposed_mutation" not in data:
        raise AdapterFactsError("proposed_mutation is required")
    proposed = _parse_proposed(data["proposed_mutation"])
    verification = _parse_verification(data.get("verification"))
    effect = concrete_effect(bound, proposed)
    digest = request_digest(bound, proposed)
    prior = _parse_prior(data.get("prior_delivery"))
    outcome = _parse_mutation(data.get("mutation"))
    failure = _parse_failure(data.get("failure"))
    retry_count, retry_budget = _parse_wait(data.get("wait"))
    resource = _require_mapping(data.get("resource", {"result": "UNKNOWN"}), "resource")
    _reject_unknown(resource, frozenset({"result"}), "resource")
    resource_result = _require_str(resource.get("result", "UNKNOWN"), "resource.result").upper()
    admission = _require_mapping(data.get("admission", {"decision": "UNKNOWN"}), "admission")
    _reject_unknown(admission, frozenset({"decision", "deny_class"}), "admission")
    admission_decision = _require_str(admission.get("decision", "UNKNOWN"), "admission.decision").upper()
    deny_class = str(admission.get("deny_class") or "").upper()

    if bound["requested_action"] not in ALLOWED_ACTIONS:
        return _emit(
            "AUTHORITY_DENIED",
            "requested_action is outside the typed coordinator action set",
            digest,
            authorize_write=False,
        )

    mismatches = []
    if authoritative["target_repo"] != bound["target_repo"]:
        mismatches.append("target_repo")
    if authoritative["workstream"] != bound["workstream"]:
        mismatches.append("workstream")
    if authoritative["intent_revision"] != bound["intent_revision"]:
        mismatches.append("intent_revision")
    if authoritative["subject_head"] != bound["subject_head"]:
        mismatches.append("subject_head")
    if authoritative["branch"] != bound["branch"]:
        mismatches.append("branch")
    if authoritative["status"] != bound["expected_status"]:
        mismatches.append("status")
    if mismatches:
        return _emit(
            "STALE_WORKER",
            "authoritative packet no longer matches the bound request: " + ",".join(mismatches),
            digest,
            authorize_write=False,
        )

    if outcome in AMBIGUOUS_OUTCOMES:
        return _emit(
            "RECONCILE_AMBIGUOUS",
            "external mutation outcome is unknown; reconcile durable state before any retry",
            digest,
            authorize_write=False,
        )
    if failure == "SEMANTIC":
        return _emit(
            "FAILED_SEMANTIC",
            "semantic failure must be re-planned rather than rewritten",
            digest,
            authorize_write=False,
        )
    if resource_result == "BLOCK" or (admission_decision == "DENY" and deny_class == "HOST_BUDGET"):
        return _emit(
            "RESOURCE_BLOCKED",
            "resource or host admission blocks the effect and does not stop unrelated sessions",
            digest,
            authorize_write=False,
        )
    if admission_decision != "ALLOW":
        return _emit(
            "RESOURCE_BLOCKED",
            "admission does not allow the effect",
            digest,
            authorize_write=False,
        )
    if prior == digest or outcome == "APPLIED":
        return _emit(
            "NO_CHANGE",
            "the same authorized request was already applied",
            digest,
            authorize_write=False,
        )
    if failure == "TRANSIENT":
        if retry_count < retry_budget and outcome == "NOT_SENT":
            return _emit(
                "TRANSIENT_RETRYABLE",
                "transient failure stayed before send and remains inside the retry budget",
                digest,
                authorize_write=False,
            )
        return _emit(
            "RECONCILE_AMBIGUOUS",
            "transient retry is not authorized after the budget or after a send attempt",
            digest,
            authorize_write=False,
        )
    gate = _authorize_concrete_effect(verification, effect, consume_replay=consume_replay)
    if gate == "REPLAY":
        return _emit(
            "NO_CHANGE",
            "trusted dispatch was already consumed for this concrete effect",
            digest,
            authorize_write=False,
        )
    if gate != "ALLOW":
        return _emit("AUTHORITY_DENIED", gate, digest, authorize_write=False)
    return _emit(
        "APPLIED",
        "host-verified dispatch matches this concrete external effect",
        digest,
        authorize_write=True,
    )


def format_report(decision: dict[str, Any]) -> str:
    fields = {
        "RESULT": decision["result"],
        "EXIT_CODE": "0",
        "REASON": decision["reason"],
        "AUTHORIZES_WRITE": "YES" if decision["authorizes_write"] else "NO",
        "WRITES": str(decision["writes"]),
        "ISSUE_MUTATED": "NO",
        "REQUEST_DIGEST": decision["request_digest"],
        "STOPS_UNRELATED_SESSIONS": "NO",
        "SPAWNS_PROCESS": "NO",
        "MINTS_AUTHORITY": "NO",
        "SENDS_NOTIFICATION": "NO",
    }
    return "\n".join(f"{key}={fields[key]}" for key in REPORT_KEYS) + "\n"


def failure_report(reason: str) -> tuple[str, int]:
    lines = [
        "RESULT=REJECT_FACTS",
        "EXIT_CODE=3",
        f"REASON={reason.replace(chr(10), ' ').strip()}",
        "AUTHORIZES_WRITE=NO",
        "WRITES=0",
        "ISSUE_MUTATED=NO",
        "STOPS_UNRELATED_SESSIONS=NO",
        "SPAWNS_PROCESS=NO",
        "MINTS_AUTHORITY=NO",
        "SENDS_NOTIFICATION=NO",
    ]
    return "\n".join(lines) + "\n", 3


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdapterFactsError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdapterFactsError(f"malformed JSON in {path}: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate",), help="Classify one bound external-write request")
    parser.add_argument("--request-json", required=True, help="JSON file of bound request and authoritative facts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_json_file(Path(args.request_json))
        text, code = format_report(evaluate(payload)), 0
    except AdapterFactsError as exc:
        text, code = failure_report(exc.reason)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    # Classification only; never spawn, mint authority, mutate GitHub, or stop sessions.
    raise SystemExit(main())
