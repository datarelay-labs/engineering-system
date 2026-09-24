#!/usr/bin/env python3
"""Pure deterministic Work Packet reconciliation and next-action planner.

Facts in, one bounded decision out. This tool does not launch or stop
workers, mutate GitHub, merge, send notifications, spawn processes, or
schedule work. Notification output is intent only.

Command:
  plan    Emit exactly one next action for one packet's structured facts

Exit status:
  0  a planner decision was emitted
  3  facts were missing, malformed, or contained an execution key
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

PACKET_STATUSES = frozenset({"ACTIVE", "PAUSED", "BLOCKED", "COMPLETE"})
PRIORITIES = frozenset({"URGENT", "HIGH", "NORMAL", "LOW"})
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
HIGH_RISKS = frozenset({"HIGH", "CRITICAL"})
FAILURE_CLASSES = frozenset(
    {
        "NONE",
        "TRANSIENT",
        "SEMANTIC",
        "RESOURCE",
        "AUTHORITY",
        "AMBIGUOUS_MUTATION",
        "HUMAN_REQUIRED",
    }
)
WORKER_STATUSES = frozenset(
    {"CLAIMED", "ACTIVE", "WAIT", "YIELD", "RETRY", "COMPLETE", "ABANDON", "SUPERSEDED", "RELEASED"}
)
ACTIVE_WORKER_STATUSES = frozenset({"CLAIMED", "ACTIVE", "WAIT", "YIELD", "RETRY"})
RESOURCE_RESULTS = frozenset({"PASS", "WARN", "BLOCK", "UNKNOWN"})
ADMISSION_DECISIONS = frozenset({"ALLOW", "DENY", "UNKNOWN"})
AUDIT_RESULTS = frozenset({"UNKNOWN", "PASS", "FAIL"})
PR_STATES = frozenset({"OPEN", "MERGED", "CLOSED"})
CI_STATES = frozenset({"UNKNOWN", "PENDING", "PASS", "FAIL"})
REVIEW_STATES = frozenset({"UNKNOWN", "OPEN", "CLEAR"})
ROLLOUT_GATES = frozenset({"NOT_REQUIRED", "PENDING", "AUTHORIZED"})
DECISIONS = frozenset(
    {
        "NOOP_COMPLETE",
        "NOOP_PAUSED",
        "BLOCK_HUMAN",
        "WAIT_EXTERNAL",
        "RECONCILE_AMBIGUOUS",
        "ADMIT_IMPLEMENTATION",
        "RESUME_WORKER",
        "AUDIT_DIRTY_TREE",
        "AUTHORIZE_PUBLICATION",
        "WAIT_EXACT_HEAD_CI",
        "AUDIT_REVIEW",
        "MERGE_READY",
        "REPLAN_SEMANTIC_FAILURE",
        "YIELD_RESOURCE",
        "STALE_WORKER",
    }
)
WAIT_DECISIONS = frozenset({"WAIT_EXTERNAL", "WAIT_EXACT_HEAD_CI"})
QUIET_MICROSTEPS = frozenset({"RESUME_WORKER"})
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
TOP_LEVEL_KEYS = frozenset(
    {
        "packet",
        "worker",
        "git",
        "resource",
        "admission",
        "mutation",
        "failure",
        "publication",
        "pr",
        "ci",
        "review",
        "wait",
        "gates",
        "previous_decision",
    }
)

REPORT_KEYS = (
    "DECISION",
    "EXIT_CODE",
    "REASON",
    "DECISION_CLASS",
    "NOTIFICATION",
    "NOTIFICATION_KEY",
    "NOTIFICATION_SUPPRESS_REASON",
    "SUBJECT_VERSION",
    "LAUNCHES_WORKER",
    "MUTATES_EXISTING_SESSIONS",
    "STOPS_UNRELATED_SESSIONS",
    "SPAWNS_PROCESS",
    "MUTATES_GITHUB",
    "SENDS_NOTIFICATION",
    "PRIORITY",
    "CHANGE_RISK",
    "INTENT_REVISION",
)


class PlannerFactsError(Exception):
    def __init__(self, reason: str, deny_class: str = "AMBIGUOUS_FACTS"):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlannerFactsError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlannerFactsError(f"{label} must be a non-empty string")
    return value.strip()


def _require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlannerFactsError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise PlannerFactsError(f"{label} must be >= {minimum}")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise PlannerFactsError(f"{label} must be a boolean")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlannerFactsError(f"{label} must be a JSON array")
    return value


def _optional_mapping(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    if key not in payload:
        return None
    return _require_mapping(payload[key], key)


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise PlannerFactsError(f"{label} contains unknown key {unknown[0]!r}")


def _enum(value: Any, label: str, allowed: frozenset[str]) -> str:
    text = _require_str(value, label).upper()
    if text not in allowed:
        raise PlannerFactsError(f"{label} is unknown: {text}")
    return text


def reject_execution_keys(payload: dict[str, Any], *, path: str = "facts") -> None:
    for key in payload:
        lowered = str(key).strip().lower()
        if lowered in FORBIDDEN_REQUEST_KEYS:
            raise PlannerFactsError(
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


def _sha_or_unknown(value: Any, label: str) -> str:
    text = _require_str(value, label)
    if text == "UNKNOWN":
        return "UNKNOWN"
    lowered = text.lower()
    if not FULL_SHA_RE.fullmatch(lowered):
        raise PlannerFactsError(f"{label} must be UNKNOWN or a 40-char lowercase hex Git SHA")
    return lowered


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlannerFactsError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlannerFactsError(f"malformed JSON in {path}: {exc}") from exc


def _parse_packet(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "packet")
    _reject_unknown(
        data,
        frozenset(
            {
                "repository",
                "workstream",
                "status",
                "priority",
                "intent_revision",
                "change_risk",
                "task_kind",
                "dependencies",
            }
        ),
        "packet",
    )
    repository = _require_str(data.get("repository"), "packet.repository")
    if not REPOSITORY_RE.fullmatch(repository):
        raise PlannerFactsError("packet.repository must be owner/name")
    workstream = _require_str(data.get("workstream"), "packet.workstream")
    if not WORKSTREAM_RE.fullmatch(workstream):
        raise PlannerFactsError("packet.workstream must be a stable slug without separators")
    status = _enum(data.get("status"), "packet.status", PACKET_STATUSES)
    priority = _enum(data.get("priority", "NORMAL"), "packet.priority", PRIORITIES)
    change_risk = _enum(data.get("change_risk", "MEDIUM"), "packet.change_risk", CHANGE_RISKS)
    intent_revision = _require_int(data.get("intent_revision"), "packet.intent_revision", minimum=1)
    task_kind = None
    if "task_kind" in data:
        task_kind = _require_str(data.get("task_kind"), "packet.task_kind")
    dependencies: list[dict[str, str]] | None
    if "dependencies" not in data:
        dependencies = None
    else:
        items = _require_list(data.get("dependencies"), "packet.dependencies")
        dependencies = []
        for index, item in enumerate(items):
            dep = _require_mapping(item, f"packet.dependencies[{index}]")
            _reject_unknown(dep, frozenset({"workstream", "status"}), f"packet.dependencies[{index}]")
            dep_status = _enum(dep.get("status"), f"packet.dependencies[{index}].status", PACKET_STATUSES)
            dependencies.append(
                {
                    "workstream": _require_str(dep.get("workstream"), f"packet.dependencies[{index}].workstream"),
                    "status": dep_status,
                }
            )
    return {
        "repository": repository,
        "workstream": workstream,
        "status": status,
        "priority": priority,
        "intent_revision": intent_revision,
        "change_risk": change_risk,
        "task_kind": task_kind,
        "dependencies": dependencies,
    }


def _parse_worker(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"present": False, "status": None, "starting_intent_revision": None, "progress_evidence": False}
    _reject_unknown(
        raw,
        frozenset({"present", "status", "starting_intent_revision", "progress_evidence"}),
        "worker",
    )
    present = _require_bool(raw.get("present"), "worker.present")
    if not present:
        if set(raw) - {"present"}:
            raise PlannerFactsError("worker fields other than present require worker.present true")
        return {"present": False, "status": None, "starting_intent_revision": None, "progress_evidence": False}
    status = _enum(raw.get("status"), "worker.status", WORKER_STATUSES)
    starting = _require_int(raw.get("starting_intent_revision"), "worker.starting_intent_revision", minimum=1)
    progress = False
    if "progress_evidence" in raw:
        progress = _require_bool(raw.get("progress_evidence"), "worker.progress_evidence")
    return {
        "present": True,
        "status": status,
        "starting_intent_revision": starting,
        "progress_evidence": progress,
    }


def _parse_git(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"head": "UNKNOWN", "dirty": None, "unpushed": None, "yielded_for_audit": False}
    _reject_unknown(raw, frozenset({"head", "dirty", "unpushed", "yielded_for_audit"}), "git")
    head = _sha_or_unknown(raw.get("head", "UNKNOWN"), "git.head") if "head" in raw else "UNKNOWN"
    dirty = _require_bool(raw.get("dirty"), "git.dirty") if "dirty" in raw else None
    unpushed = _require_bool(raw.get("unpushed"), "git.unpushed") if "unpushed" in raw else None
    yielded = _require_bool(raw.get("yielded_for_audit"), "git.yielded_for_audit") if "yielded_for_audit" in raw else False
    return {"head": head, "dirty": dirty, "unpushed": unpushed, "yielded_for_audit": yielded}


def _parse_resource(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"result": "UNKNOWN"}
    _reject_unknown(raw, frozenset({"result"}), "resource")
    return {"result": _enum(raw.get("result", "UNKNOWN"), "resource.result", RESOURCE_RESULTS)}


def _parse_admission(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"decision": "UNKNOWN", "deny_class": None}
    _reject_unknown(raw, frozenset({"decision", "deny_class"}), "admission")
    decision = _enum(raw.get("decision", "UNKNOWN"), "admission.decision", ADMISSION_DECISIONS)
    deny_class = None
    if "deny_class" in raw and raw.get("deny_class") is not None:
        deny_class = _require_str(raw.get("deny_class"), "admission.deny_class")
    return {"decision": decision, "deny_class": deny_class}


def _parse_mutation(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"ambiguous": False, "kind": None}
    _reject_unknown(raw, frozenset({"ambiguous", "kind"}), "mutation")
    ambiguous = _require_bool(raw.get("ambiguous", False), "mutation.ambiguous")
    kind = None
    if "kind" in raw and raw.get("kind") is not None:
        kind = _require_str(raw.get("kind"), "mutation.kind")
    return {"ambiguous": ambiguous, "kind": kind}


def _parse_failure(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"class": "NONE", "identical_semantic_count": 0}
    _reject_unknown(raw, frozenset({"class", "identical_semantic_count"}), "failure")
    failure_class = _enum(raw.get("class", "NONE"), "failure.class", FAILURE_CLASSES)
    count = 0
    if "identical_semantic_count" in raw:
        count = _require_int(raw.get("identical_semantic_count"), "failure.identical_semantic_count", minimum=0)
    return {"class": failure_class, "identical_semantic_count": count}


def _parse_publication(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"coordinator_audit": "UNKNOWN", "authorized_intent_revision": None}
    _reject_unknown(raw, frozenset({"coordinator_audit", "authorized_intent_revision"}), "publication")
    audit = _enum(raw.get("coordinator_audit", "UNKNOWN"), "publication.coordinator_audit", AUDIT_RESULTS)
    authorized = None
    if "authorized_intent_revision" in raw and raw.get("authorized_intent_revision") is not None:
        authorized = _require_int(
            raw.get("authorized_intent_revision"),
            "publication.authorized_intent_revision",
            minimum=1,
        )
    return {"coordinator_audit": audit, "authorized_intent_revision": authorized}


def _parse_pr(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"exists": False, "state": None, "head": "UNKNOWN", "mergeable": "UNKNOWN"}
    _reject_unknown(raw, frozenset({"exists", "state", "head", "mergeable"}), "pr")
    exists = _require_bool(raw.get("exists"), "pr.exists")
    if not exists:
        if set(raw) - {"exists"}:
            raise PlannerFactsError("pr fields other than exists require pr.exists true")
        return {"exists": False, "state": None, "head": "UNKNOWN", "mergeable": "UNKNOWN"}
    state = _enum(raw.get("state"), "pr.state", PR_STATES)
    head = _sha_or_unknown(raw.get("head", "UNKNOWN"), "pr.head") if "head" in raw else "UNKNOWN"
    mergeable = _enum(raw.get("mergeable", "UNKNOWN"), "pr.mergeable", frozenset({"YES", "NO", "UNKNOWN"}))
    return {"exists": True, "state": state, "head": head, "mergeable": mergeable}


def _parse_ci(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"state": "UNKNOWN", "subject_head": "UNKNOWN"}
    _reject_unknown(raw, frozenset({"state", "subject_head"}), "ci")
    state = _enum(raw.get("state", "UNKNOWN"), "ci.state", CI_STATES)
    subject = _sha_or_unknown(raw.get("subject_head", "UNKNOWN"), "ci.subject_head") if "subject_head" in raw else "UNKNOWN"
    return {"state": state, "subject_head": subject}


def _parse_review(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"actionable_open": None, "state": "UNKNOWN", "subject_head": "UNKNOWN"}
    _reject_unknown(raw, frozenset({"actionable_open", "state", "subject_head"}), "review")
    actionable = None
    if "actionable_open" in raw:
        actionable = _require_bool(raw.get("actionable_open"), "review.actionable_open")
    state = _enum(raw.get("state", "UNKNOWN"), "review.state", REVIEW_STATES)
    subject = _sha_or_unknown(raw.get("subject_head", "UNKNOWN"), "review.subject_head") if "subject_head" in raw else "UNKNOWN"
    return {"actionable_open": actionable, "state": state, "subject_head": subject}


def _parse_wait(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"external": False, "condition": None, "retry_count": 0, "retry_budget": 3}
    _reject_unknown(raw, frozenset({"external", "condition", "retry_count", "retry_budget"}), "wait")
    external = _require_bool(raw.get("external", False), "wait.external")
    condition = None
    if "condition" in raw and raw.get("condition") is not None:
        condition = _require_str(raw.get("condition"), "wait.condition")
    retry_count = _require_int(raw.get("retry_count", 0), "wait.retry_count", minimum=0)
    retry_budget = _require_int(raw.get("retry_budget", 3), "wait.retry_budget", minimum=0)
    return {
        "external": external,
        "condition": condition,
        "retry_count": retry_count,
        "retry_budget": retry_budget,
    }


def _parse_gates(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {"org_rollout": "NOT_REQUIRED"}
    _reject_unknown(raw, frozenset({"org_rollout"}), "gates")
    return {"org_rollout": _enum(raw.get("org_rollout", "NOT_REQUIRED"), "gates.org_rollout", ROLLOUT_GATES)}


def _parse_previous(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    _reject_unknown(raw, frozenset({"key", "decision"}), "previous_decision")
    key = _require_str(raw.get("key"), "previous_decision.key") if "key" in raw else ""
    decision = _require_str(raw.get("decision"), "previous_decision.decision") if "decision" in raw else ""
    if not key:
        return None
    return {"key": key, "decision": decision}


def normalize_facts(payload: dict[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, "facts")
    reject_execution_keys(data)
    _reject_unknown(data, TOP_LEVEL_KEYS, "facts")
    if "packet" not in data:
        raise PlannerFactsError("facts.packet is required")
    packet = _parse_packet(data["packet"])
    return {
        "packet": packet,
        "worker": _parse_worker(_optional_mapping(data, "worker")),
        "git": _parse_git(_optional_mapping(data, "git")),
        "resource": _parse_resource(_optional_mapping(data, "resource")),
        "admission": _parse_admission(_optional_mapping(data, "admission")),
        "mutation": _parse_mutation(_optional_mapping(data, "mutation")),
        "failure": _parse_failure(_optional_mapping(data, "failure")),
        "publication": _parse_publication(_optional_mapping(data, "publication")),
        "pr": _parse_pr(_optional_mapping(data, "pr")),
        "ci": _parse_ci(_optional_mapping(data, "ci")),
        "review": _parse_review(_optional_mapping(data, "review")),
        "wait": _parse_wait(_optional_mapping(data, "wait")),
        "gates": _parse_gates(_optional_mapping(data, "gates")),
        "previous_decision": _parse_previous(_optional_mapping(data, "previous_decision")),
    }


def decision_class(decision: str) -> str:
    if decision in WAIT_DECISIONS:
        return "WAIT"
    return decision


def subject_version(facts: dict[str, Any]) -> str:
    for value in (facts["pr"]["head"], facts["ci"]["subject_head"], facts["git"]["head"]):
        if value != "UNKNOWN" and FULL_SHA_RE.fullmatch(value):
            return value
    return "NONE"


def notification_key(facts: dict[str, Any], decision: str) -> str:
    packet = facts["packet"]
    return "|".join(
        (
            packet["repository"],
            packet["workstream"],
            str(packet["intent_revision"]),
            decision_class(decision),
            subject_version(facts),
        )
    )


def _dependencies_eligible(packet: dict[str, Any]) -> str:
    dependencies = packet["dependencies"]
    if dependencies is None:
        return "UNKNOWN"
    if not dependencies:
        return "YES"
    if all(item["status"] == "COMPLETE" for item in dependencies):
        return "YES"
    return "NO"


def _active_worker(facts: dict[str, Any]) -> bool:
    worker = facts["worker"]
    packet = facts["packet"]
    return bool(
        worker["present"]
        and worker["status"] in ACTIVE_WORKER_STATUSES
        and worker["starting_intent_revision"] == packet["intent_revision"]
    )


def _emit(
    facts: dict[str, Any],
    decision: str,
    reason: str,
    *,
    subject: str | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise PlannerFactsError(f"internal decision is unknown: {decision}")
    packet = facts["packet"]
    version = subject if subject is not None else subject_version(facts)
    key = "|".join(
        (
            packet["repository"],
            packet["workstream"],
            str(packet["intent_revision"]),
            decision_class(decision),
            version,
        )
    )
    previous = facts["previous_decision"]
    same = previous is not None and previous["key"] == key
    if decision in QUIET_MICROSTEPS:
        disposition, suppress = "SUPPRESS", "WORKER_MICROSTEP"
    elif decision in WAIT_DECISIONS and same:
        disposition, suppress = "SUPPRESS", "IDENTICAL_WAIT"
    elif same:
        disposition, suppress = "SUPPRESS", "DEDUP"
    else:
        disposition, suppress = "SEND", "NONE"
    return {
        "schema_version": 1,
        "decision": decision,
        "reason": reason,
        "decision_class": decision_class(decision),
        "notification_disposition": disposition,
        "notification_key": key,
        "notification_suppress_reason": suppress,
        "subject_version": version,
        "launches_worker": decision == "ADMIT_IMPLEMENTATION",
        "mutates_existing_sessions": False,
        "stops_unrelated_sessions": False,
        "spawns_process": False,
        "mutates_github": False,
        "sends_notification": False,
        "priority": packet["priority"],
        "change_risk": packet["change_risk"],
        "intent_revision": packet["intent_revision"],
        "repository": packet["repository"],
        "workstream": packet["workstream"],
    }


def _audit_or_authorize(facts: dict[str, Any]) -> dict[str, Any]:
    publication = facts["publication"]
    intent = facts["packet"]["intent_revision"]
    if publication["coordinator_audit"] == "FAIL":
        return _emit(
            facts,
            "REPLAN_SEMANTIC_FAILURE",
            "coordinator audit failed; re-plan instead of publishing the same result",
        )
    authorized = publication["authorized_intent_revision"]
    if publication["coordinator_audit"] == "PASS" and authorized == intent:
        return _emit(
            facts,
            "AUTHORIZE_PUBLICATION",
            "coordinator audit PASS matches the current intent revision",
        )
    if publication["coordinator_audit"] == "PASS" and authorized != intent:
        return _emit(
            facts,
            "AUDIT_DIRTY_TREE",
            "publication authorization does not match the current intent revision",
        )
    return _emit(
        facts,
        "AUDIT_DIRTY_TREE",
        "yielded or dirty implementation requires coordinator audit before publication",
    )


def _pr_pipeline(facts: dict[str, Any]) -> dict[str, Any]:
    pr = facts["pr"]
    if pr["state"] != "OPEN":
        return _emit(facts, "WAIT_EXTERNAL", f"pull request state {pr['state']} is not an open merge candidate")
    git_head = facts["git"]["head"]
    pr_head = pr["head"]
    exact_tree = (
        git_head != "UNKNOWN"
        and pr_head != "UNKNOWN"
        and git_head == pr_head
        and FULL_SHA_RE.fullmatch(pr_head) is not None
    )
    if not exact_tree:
        return _emit(
            facts,
            "WAIT_EXACT_HEAD_CI",
            "pull request head is not bound to the observed Git HEAD",
            subject=pr_head if pr_head != "UNKNOWN" else "NONE",
        )
    ci = facts["ci"]
    ci_exact = ci["subject_head"] == pr_head
    if ci["state"] == "FAIL" and ci_exact:
        return _emit(
            facts,
            "REPLAN_SEMANTIC_FAILURE",
            "exact-head CI failed; re-plan instead of retrying publication",
            subject=pr_head,
        )
    if ci["state"] != "PASS" or not ci_exact:
        return _emit(
            facts,
            "WAIT_EXACT_HEAD_CI",
            "exact-head CI is pending or not bound to the pull request head",
            subject=pr_head,
        )
    review = facts["review"]
    review_bound = review["subject_head"] == pr_head
    actionable = review["actionable_open"] is True or review["state"] == "OPEN"
    review_clear = review["actionable_open"] is False and review["state"] == "CLEAR" and review_bound
    if not review_clear or actionable:
        return _emit(
            facts,
            "AUDIT_REVIEW",
            "exact-head CI passed with actionable or unbound review state",
            subject=pr_head,
        )
    git = facts["git"]
    if git["dirty"] is not False:
        return _emit(facts, "AUDIT_DIRTY_TREE", "open pull request worktree is dirty or unverified", subject=pr_head)
    if git["unpushed"] is not False:
        return _emit(facts, "WAIT_EXTERNAL", "unpushed commits are not proven present on the pull request head", subject=pr_head)
    if facts["gates"]["org_rollout"] == "PENDING":
        return _emit(facts, "WAIT_EXTERNAL", "org rollout authorization is a separate gate and is still pending", subject=pr_head)
    if facts["packet"]["change_risk"] in HIGH_RISKS:
        publication = facts["publication"]
        audit_ok = (
            publication["coordinator_audit"] == "PASS"
            and publication["authorized_intent_revision"] == facts["packet"]["intent_revision"]
        )
        if not audit_ok:
            return _emit(
                facts,
                "WAIT_EXTERNAL",
                "HIGH or CRITICAL merge requires coordinator audit PASS for the current intent revision",
                subject=pr_head,
            )
    if pr["mergeable"] != "YES":
        return _emit(facts, "WAIT_EXTERNAL", "pull request mergeability is not YES", subject=pr_head)
    return _emit(facts, "MERGE_READY", "exact-head CI, review, audit, and mergeability gates are green", subject=pr_head)


def _implementation_path(facts: dict[str, Any]) -> dict[str, Any]:
    git = facts["git"]
    implementing = _active_worker(facts) and not git["yielded_for_audit"]
    if git["dirty"] is True and not implementing:
        return _audit_or_authorize(facts)
    if git["yielded_for_audit"]:
        return _audit_or_authorize(facts)
    if implementing:
        if facts["worker"]["progress_evidence"]:
            return _emit(
                facts,
                "RESUME_WORKER",
                "active worker matches the packet intent revision and has machine-observable progress evidence",
            )
        return _emit(
            facts,
            "WAIT_EXTERNAL",
            "matching active worker has no machine-observable progress evidence; reconcile before resume",
        )
    if git["dirty"] is None or git["unpushed"] is None:
        return _emit(facts, "WAIT_EXTERNAL", "Git dirty or unpushed state is UNKNOWN")
    resource = facts["resource"]["result"]
    admission = facts["admission"]["decision"]
    if git["dirty"] is False and resource in {"PASS", "WARN"} and admission == "ALLOW":
        return _emit(
            facts,
            "ADMIT_IMPLEMENTATION",
            "ACTIVE packet is dependency-eligible and resource/admission facts allow one worker",
        )
    if admission == "DENY":
        return _emit(facts, "WAIT_EXTERNAL", "admission DENY blocks worker launch until facts change")
    return _emit(facts, "WAIT_EXTERNAL", "resource or admission facts do not allow worker launch")


def plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Return one planner decision for structured facts.

    The same payload always yields the same decision object. Missing
    observational facts stay UNKNOWN and cannot open a PASS/ALLOW/merge gate.
    """
    facts = normalize_facts(payload)
    packet = facts["packet"]
    status = packet["status"]
    if status == "COMPLETE":
        return _emit(facts, "NOOP_COMPLETE", "COMPLETE packets do not launch workers")
    if status == "PAUSED":
        return _emit(facts, "NOOP_PAUSED", "PAUSED packets do not launch workers")
    if status == "BLOCKED":
        return _emit(facts, "BLOCK_HUMAN", "BLOCKED packets stay with the owner and do not launch workers")

    if facts["mutation"]["ambiguous"] or facts["failure"]["class"] == "AMBIGUOUS_MUTATION":
        kind = facts["mutation"]["kind"] or "mutation"
        return _emit(facts, "RECONCILE_AMBIGUOUS", f"ambiguous {kind} outcome must be reconciled before retry")

    worker = facts["worker"]
    if (
        worker["present"]
        and worker["status"] in ACTIVE_WORKER_STATUSES
        and worker["starting_intent_revision"] != packet["intent_revision"]
    ):
        return _emit(
            facts,
            "STALE_WORKER",
            "worker intent revision does not match the packet; do not finalize or publish",
        )

    failure = facts["failure"]
    if failure["class"] == "SEMANTIC" or failure["identical_semantic_count"] >= 2:
        return _emit(
            facts,
            "REPLAN_SEMANTIC_FAILURE",
            "semantic failure requires a new plan rather than another identical attempt",
        )
    if failure["class"] in {"HUMAN_REQUIRED", "AUTHORITY"}:
        return _emit(facts, "BLOCK_HUMAN", f"failure class {failure['class']} requires the owner")

    if (
        facts["resource"]["result"] == "BLOCK"
        or failure["class"] == "RESOURCE"
        or (facts["admission"]["decision"] == "DENY" and facts["admission"]["deny_class"] == "HOST_BUDGET")
    ):
        return _emit(
            facts,
            "YIELD_RESOURCE",
            "resource preflight BLOCK yields until capacity facts change and does not stop unrelated sessions",
        )

    if failure["class"] == "TRANSIENT":
        if facts["wait"]["retry_count"] < facts["wait"]["retry_budget"]:
            return _emit(facts, "WAIT_EXTERNAL", "transient failure stays inside the bounded retry budget")
        return _emit(facts, "BLOCK_HUMAN", "transient retry budget is exhausted")

    eligibility = _dependencies_eligible(packet)
    if eligibility != "YES":
        return _emit(facts, "WAIT_EXTERNAL", f"dependencies are {eligibility} and cannot start work")

    if facts["pr"]["exists"]:
        return _pr_pipeline(facts)

    if facts["wait"]["external"]:
        condition = facts["wait"]["condition"] or "external condition"
        return _emit(facts, "WAIT_EXTERNAL", f"waiting for {condition}")

    return _implementation_path(facts)


def format_report(decision: dict[str, Any], *, exit_code: int = 0, deny_class: str | None = None) -> str:
    fields = {
        "DECISION": decision["decision"],
        "EXIT_CODE": str(exit_code),
        "REASON": decision["reason"],
        "DECISION_CLASS": decision["decision_class"],
        "NOTIFICATION": decision["notification_disposition"],
        "NOTIFICATION_KEY": decision["notification_key"],
        "NOTIFICATION_SUPPRESS_REASON": decision["notification_suppress_reason"],
        "SUBJECT_VERSION": decision["subject_version"],
        "LAUNCHES_WORKER": "YES" if decision["launches_worker"] else "NO",
        "MUTATES_EXISTING_SESSIONS": "NO",
        "STOPS_UNRELATED_SESSIONS": "NO",
        "SPAWNS_PROCESS": "NO",
        "MUTATES_GITHUB": "NO",
        "SENDS_NOTIFICATION": "NO",
        "PRIORITY": decision["priority"],
        "CHANGE_RISK": decision["change_risk"],
        "INTENT_REVISION": str(decision["intent_revision"]),
    }
    if deny_class:
        fields["DENY_CLASS"] = deny_class
    lines = [f"{key}={fields[key]}" for key in REPORT_KEYS if key in fields]
    if deny_class:
        lines.append(f"DENY_CLASS={deny_class}")
    return "\n".join(lines) + "\n"


def failure_report(reason: str, deny_class: str = "AMBIGUOUS_FACTS") -> tuple[str, int]:
    lines = [
        "DECISION=REJECT_FACTS",
        "EXIT_CODE=3",
        f"REASON={reason.replace(chr(10), ' ').strip()}",
        f"DENY_CLASS={deny_class}",
        "LAUNCHES_WORKER=NO",
        "MUTATES_EXISTING_SESSIONS=NO",
        "STOPS_UNRELATED_SESSIONS=NO",
        "SPAWNS_PROCESS=NO",
        "MUTATES_GITHUB=NO",
        "SENDS_NOTIFICATION=NO",
    ]
    return "\n".join(lines) + "\n", 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan",), help="Emit one next action from structured facts")
    parser.add_argument("--facts", required=True, help="JSON file of machine-readable coordinator facts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_json_file(Path(args.facts))
        decision = plan(payload)
        text, code = format_report(decision), 0
    except PlannerFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    # Fact evaluation only; never spawn, mutate GitHub, merge, notify, or stop sessions.
    raise SystemExit(main())
