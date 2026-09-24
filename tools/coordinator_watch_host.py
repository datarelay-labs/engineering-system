#!/usr/bin/env python3
"""Run-once host for one coordinator watch.

Acquires a single-instance lock, reads one declared watch's fact documents,
calls the pure watch evaluator, and delivers at most one already-authorized
typed action after a fresh reconciliation read. Cadence stays with an external
scheduler. This host does not accept caller commands or URLs, mint authority,
mutate GitHub, merge, stop sessions, sleep, or busy-loop.

Command:
  run-once  Reconcile one watch and deliver at most one typed action

Exit status:
  0  a host result was emitted
  3  the request or fact documents were missing, malformed, or execution-keyed
     (DENY_CLASS=EXECUTION_FORBIDDEN when an execution key is present)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from coordinator import PlannerFactsError, normalize_facts, reject_execution_keys, subject_version
from coordinator_watch import (
    WATCH_CLASSES,
    WatchFactsError,
    evaluate,
    load_json_file,
)
from worker_adapter import (
    AdapterFactsError,
    _authorize_concrete_effect,
    concrete_effect,
    evaluate as evaluate_write,
)

HOST_RESULTS = frozenset(
    {
        "NO_ACTION",
        "DELIVERED",
        "DEDUP",
        "LOCK_HELD",
        "STALE_RECONCILE",
        "RESOURCE_BLOCKED",
        "RECONCILE_AMBIGUOUS",
        "AUTHORITY_DENIED",
    }
)
TYPED_RESULTS = frozenset({"WAKE_COORDINATOR", "RESUME_ADMITTED_WORKER", "NOTIFY_OWNER"})
WRITE_ACTIONS = {
    "WAKE_COORDINATOR": "comment_work_packet",
    "RESUME_ADMITTED_WORKER": "update_work_packet",
}
AMBIGUOUS_OUTCOMES = frozenset({"AMBIGUOUS", "UNKNOWN", "TIMEOUT"})
REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "target_repo",
        "workstream",
        "watch_class",
        "branch",
        "effect_target_id",
        "lock_path",
        "watch_state_path",
        "ledger_path",
        "collected_facts_path",
        "authoritative_facts_path",
        "work_budget",
        "verification",
    }
)
EXTRA_FORBIDDEN_KEYS = frozenset(
    {
        "endpoint",
        "url",
        "uri",
        "webhook",
        "signing_key",
        "hmac_secret",
        "private_key",
        "mint",
        "prompt",
        "chat",
        "secret",
        "token",
        "password",
    }
)
LEDGER_ENTRY_KEYS = frozenset({"key", "outcome", "result"})
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_LEDGER_ENTRIES = 32
MAX_TEXT = 512
REPORT_KEYS = (
    "RESULT",
    "EXIT_CODE",
    "REASON",
    "WATCH_RESULT",
    "ACTIONS_DELIVERED",
    "WAKES",
    "RESUMES",
    "NOTIFICATIONS",
    "NOTIFICATION_LEVEL",
    "NOTIFICATION_DELIVERY",
    "LOCK",
    "NEXT_ELIGIBLE_CHECK_AT",
    "STOPS_UNRELATED_SESSIONS",
    "MUTATES_EXISTING_SESSIONS",
    "SPAWNS_PROCESS",
    "MUTATES_GITHUB",
    "EXECUTES_COMMAND",
    "BUSY_LOOP",
    "SLEEPS",
    "CADENCE",
)


class HostRequestError(Exception):
    def __init__(self, reason: str, deny_class: str = "AMBIGUOUS_FACTS"):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HostRequestError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HostRequestError(f"{label} must be a non-empty string")
    return value.strip()


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise HostRequestError(f"{label} contains unknown key {unknown[0]!r}")


def _reject_execution(payload: Any, label: str) -> None:
    if isinstance(payload, dict):
        try:
            reject_execution_keys(payload, path=label)
        except PlannerFactsError as exc:
            raise HostRequestError(exc.reason, exc.deny_class) from exc
        for key, value in payload.items():
            if str(key).strip().lower() in EXTRA_FORBIDDEN_KEYS:
                raise HostRequestError(
                    f"{label} contains forbidden execution key {key!r}",
                    "EXECUTION_FORBIDDEN",
                )
            _reject_execution(value, f"{label}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _reject_execution(item, f"{label}[{index}]")


def _bounded_text(value: str, label: str) -> str:
    if len(value) > MAX_TEXT or "\x00" in value or "\n" in value:
        raise HostRequestError(f"{label} is not a bounded single-line value")
    return value


def effect_content(watch_result: dict[str, Any]) -> str:
    """Bounded metadata for one typed effect. No caller prose, logs, or source."""
    lines = (
        f"RESULT={watch_result['result']}",
        f"COORDINATOR_DECISION={watch_result['coordinator_decision']}",
        f"NOTIFICATION_KEY={watch_result['notification_key']}",
        f"WATCH_CLASS={watch_result['watch_class']}",
        f"INTENT_REVISION={watch_result['intent_revision']}",
        f"SUBJECT={watch_result['next_watch_state']['subject_version']}",
    )
    return "\n".join(lines) + "\n"


def external_effect(
    watch_result: dict[str, Any],
    *,
    repository: str,
    workstream: str,
    intent_revision: int,
    subject_head: str,
    status: str,
    branch: str,
    effect_target_id: str,
) -> dict[str, Any]:
    """Concrete worker-adapter effect the host will authorize for this result."""
    action = WRITE_ACTIONS.get(str(watch_result.get("result")))
    if action is None:
        raise HostRequestError("watch result has no external write action")
    bound = {
        "target_repo": repository,
        "workstream": workstream,
        "intent_revision": intent_revision,
        "subject_head": subject_head,
        "requested_action": action,
        "branch": branch,
        "expected_status": status,
    }
    proposed = {
        "target": {"kind": "issue", "id": effect_target_id},
        "content": effect_content(watch_result),
    }
    return concrete_effect(bound, proposed)


def owner_notice_effect(watch_result: dict[str, Any]) -> dict[str, Any]:
    """Bounded INFO notice. COMPLETE is reserved for whole-packet completion."""
    return {
        "coordinator_decision": watch_result["coordinator_decision"],
        "intent_revision": watch_result["intent_revision"],
        "kind": "owner_notice",
        "level": "INFO",
        "notification_key": watch_result["notification_key"],
        "subject_version": watch_result["next_watch_state"]["subject_version"],
        "target_repo": watch_result["repository"],
        "watch_class": watch_result["watch_class"],
        "watch_result": watch_result["result"],
        "workstream": watch_result["workstream"],
    }


def _parse_request(raw: dict[str, Any]) -> dict[str, Any]:
    _reject_execution(raw, "request")
    _reject_unknown(raw, REQUEST_KEYS, "request")
    if raw.get("schema_version") != 1:
        raise HostRequestError("request.schema_version must be 1")
    repository = _bounded_text(_require_str(raw.get("target_repo"), "request.target_repo"), "request.target_repo")
    if not REPOSITORY_RE.fullmatch(repository):
        raise HostRequestError("request.target_repo must be owner/name")
    workstream = _bounded_text(_require_str(raw.get("workstream"), "request.workstream"), "request.workstream")
    if not WORKSTREAM_RE.fullmatch(workstream):
        raise HostRequestError("request.workstream must be a stable slug")
    watch_class = _require_str(raw.get("watch_class"), "request.watch_class")
    if watch_class not in WATCH_CLASSES:
        raise HostRequestError(f"request.watch_class is unknown: {watch_class}")
    branch = _bounded_text(_require_str(raw.get("branch"), "request.branch"), "request.branch")
    if not BRANCH_RE.fullmatch(branch):
        raise HostRequestError("request.branch contains unsupported characters")
    target_id = _bounded_text(
        _require_str(raw.get("effect_target_id"), "request.effect_target_id"),
        "request.effect_target_id",
    )
    if not TARGET_RE.fullmatch(target_id):
        raise HostRequestError("request.effect_target_id contains unsupported characters")
    budget = raw.get("work_budget")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget != 1:
        raise HostRequestError("request.work_budget must be 1")
    verification = None
    if "verification" in raw and raw.get("verification") is not None:
        data = _require_mapping(raw.get("verification"), "verification")
        _reject_unknown(data, frozenset({"binding_assertion", "dispatch_assertion"}), "verification")
        verification = {
            "binding_assertion": Path(_require_str(data.get("binding_assertion"), "verification.binding_assertion")),
            "dispatch_assertion": Path(
                _require_str(data.get("dispatch_assertion"), "verification.dispatch_assertion")
            ),
        }
    paths = {
        "lock_path": Path(_require_str(raw.get("lock_path"), "request.lock_path")),
        "watch_state_path": Path(_require_str(raw.get("watch_state_path"), "request.watch_state_path")),
        "ledger_path": Path(_require_str(raw.get("ledger_path"), "request.ledger_path")),
        "collected_facts_path": Path(_require_str(raw.get("collected_facts_path"), "request.collected_facts_path")),
        "authoritative_facts_path": Path(
            _require_str(raw.get("authoritative_facts_path"), "request.authoritative_facts_path")
        ),
    }
    if paths["watch_state_path"] == paths["ledger_path"]:
        raise HostRequestError("watch state and ledger must be distinct files")
    return {
        "target_repo": repository,
        "workstream": workstream,
        "watch_class": watch_class,
        "branch": branch,
        "effect_target_id": target_id,
        "work_budget": 1,
        "verification": verification,
        **paths,
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HostRequestError(f"{label} is missing: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostRequestError(f"{label} is unreadable") from exc
    data = _require_mapping(payload, label)
    _reject_execution(data, label)
    return data


def _load_watch_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json_object(path, "watch_state")


def _load_facts(path: Path, watch_class: str, repository: str, workstream: str) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _read_json_object(path, "facts")
    watch = _require_mapping(raw.get("watch"), "facts.watch")
    if watch.get("watch_class") != watch_class:
        raise HostRequestError("facts.watch.watch_class does not match the declared watch")
    planner = {key: value for key, value in raw.items() if key != "watch"}
    try:
        facts = normalize_facts(planner)
    except PlannerFactsError as exc:
        raise HostRequestError(exc.reason, exc.deny_class) from exc
    packet = facts["packet"]
    if packet["repository"] != repository or packet["workstream"] != workstream:
        raise HostRequestError("facts do not match the declared repository or workstream")
    return raw, facts


def _snapshot(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "repository": facts["packet"]["repository"],
        "workstream": facts["packet"]["workstream"],
        "intent_revision": facts["packet"]["intent_revision"],
        "status": facts["packet"]["status"],
        "git_head": facts["git"]["head"],
        "pr_head": facts["pr"]["head"],
        "ci_subject_head": facts["ci"]["subject_head"],
        "resource": facts["resource"]["result"],
        "admission": facts["admission"]["decision"],
        "ambiguous": facts["mutation"]["ambiguous"],
        "progress_evidence": facts["worker"]["progress_evidence"],
        "starting_intent_revision": facts["worker"]["starting_intent_revision"],
    }


def _reconcile(collected: dict[str, Any], authoritative: dict[str, Any], watch_result: str) -> str | None:
    if authoritative["ambiguous"]:
        return "RECONCILE_AMBIGUOUS"
    identity = (
        "repository",
        "workstream",
        "intent_revision",
        "status",
        "git_head",
        "pr_head",
        "ci_subject_head",
        "progress_evidence",
        "starting_intent_revision",
    )
    if any(collected[key] != authoritative[key] for key in identity):
        return "STALE_RECONCILE"
    if authoritative["resource"] == "BLOCK" or authoritative["admission"] == "DENY":
        return "RESOURCE_BLOCKED"
    if collected["resource"] != authoritative["resource"] or collected["admission"] != authoritative["admission"]:
        return "STALE_RECONCILE"
    if watch_result == "RESUME_ADMITTED_WORKER" and not authoritative["progress_evidence"]:
        return "STALE_RECONCILE"
    return None


def _ledger_entries(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw = _read_json_object(path, "ledger")
    _reject_unknown(raw, frozenset({"schema_version", "applied"}), "ledger")
    if raw.get("schema_version") != 1:
        raise HostRequestError("ledger.schema_version must be 1")
    applied = raw.get("applied", [])
    if not isinstance(applied, list):
        raise HostRequestError("ledger.applied must be a JSON array")
    if len(applied) > MAX_LEDGER_ENTRIES:
        raise HostRequestError("ledger exceeds the bounded action history")
    entries: list[dict[str, str]] = []
    for index, item in enumerate(applied):
        data = _require_mapping(item, f"ledger.applied[{index}]")
        _reject_unknown(data, LEDGER_ENTRY_KEYS, f"ledger.applied[{index}]")
        key = _bounded_text(_require_str(data.get("key"), "ledger key"), "ledger key")
        outcome = _bounded_text(_require_str(data.get("outcome"), "ledger outcome"), "ledger outcome")
        result = _bounded_text(_require_str(data.get("result"), "ledger result"), "ledger result")
        entries.append({"key": key, "outcome": outcome, "result": result})
    return entries


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _reject_execution(payload, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _base_result(
    *,
    result: str,
    reason: str,
    watch_result: str = "NONE",
    actions: int = 0,
    wakes: int = 0,
    resumes: int = 0,
    notifications: int = 0,
    level: str = "NONE",
    delivery: str = "NONE",
    lock: str = "ACQUIRED",
    next_check: str | None = None,
) -> dict[str, Any]:
    if result not in HOST_RESULTS:
        raise HostRequestError(f"internal host result is unknown: {result}")
    if actions not in (0, 1) or wakes + resumes + notifications != actions:
        raise HostRequestError("internal host action count is inconsistent")
    return {
        "schema_version": 1,
        "result": result,
        "reason": reason,
        "watch_result": watch_result,
        "actions_delivered": actions,
        "wakes": wakes,
        "resumes": resumes,
        "notifications": notifications,
        "notification_level": level,
        "notification_delivery": delivery,
        "lock": lock,
        "next_eligible_check_at": next_check,
        "stops_unrelated_sessions": False,
        "mutates_existing_sessions": False,
        "spawns_process": False,
        "mutates_github": False,
        "executes_command": False,
        "busy_loop": False,
        "sleeps": False,
        "cadence": "EXTERNAL",
    }


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, b"held\n")
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def _persist_watch(path: Path, watch_result: dict[str, Any]) -> None:
    _write_json(path, watch_result["next_watch_state"])


def _persist_ledger(path: Path, entries: list[dict[str, str]]) -> None:
    if len(entries) > MAX_LEDGER_ENTRIES:
        raise HostRequestError("ledger exceeds the bounded action history")
    _write_json(path, {"schema_version": 1, "applied": entries})


def _deliver_write(request: dict[str, Any], facts: dict[str, Any], watch_result: dict[str, Any]) -> dict[str, Any]:
    subject = subject_version(facts)
    if not FULL_SHA_RE.fullmatch(subject):
        return {
            "result": "AUTHORITY_DENIED",
            "reason": "typed action subject is not an exact Git head",
        }
    packet = facts["packet"]
    bound = {
        "target_repo": packet["repository"],
        "workstream": packet["workstream"],
        "intent_revision": packet["intent_revision"],
        "subject_head": subject,
        "requested_action": WRITE_ACTIONS[watch_result["result"]],
        "branch": request["branch"],
        "expected_status": packet["status"],
    }
    proposed = {
        "target": {"kind": "issue", "id": request["effect_target_id"]},
        "content": effect_content(watch_result),
    }
    payload: dict[str, Any] = {
        "bound_request": bound,
        "authoritative": {
            "target_repo": packet["repository"],
            "workstream": packet["workstream"],
            "intent_revision": packet["intent_revision"],
            "status": packet["status"],
            "subject_head": subject,
            "branch": request["branch"],
        },
        "proposed_mutation": proposed,
        "resource": {"result": facts["resource"]["result"]},
        "admission": {"decision": facts["admission"]["decision"]},
        "mutation": {"outcome": "NOT_SENT"},
        "failure": {"class": "NONE"},
    }
    if facts["admission"].get("deny_class"):
        payload["admission"]["deny_class"] = facts["admission"]["deny_class"]
    if request["verification"] is not None:
        payload["verification"] = {
            "binding_assertion": str(request["verification"]["binding_assertion"]),
            "dispatch_assertion": str(request["verification"]["dispatch_assertion"]),
        }
    return evaluate_write(payload)


def _deliver_notice(request: dict[str, Any], watch_result: dict[str, Any]) -> str:
    if request["verification"] is None:
        return "trusted verification assertions are missing; caller-supplied permission or dispatch facts are not authority"
    return _authorize_concrete_effect(request["verification"], owner_notice_effect(watch_result))


def _counts(watch_name: str) -> dict[str, int]:
    return {
        "actions": 1,
        "wakes": 1 if watch_name == "WAKE_COORDINATOR" else 0,
        "resumes": 1 if watch_name == "RESUME_ADMITTED_WORKER" else 0,
        "notifications": 1 if watch_name == "NOTIFY_OWNER" else 0,
    }


def _finish_typed(
    request: dict[str, Any],
    watch_result: dict[str, Any],
    authoritative: dict[str, Any],
    entries: list[dict[str, str]],
) -> dict[str, Any]:
    name = watch_result["result"]
    key = str(watch_result["notification_key"])
    prior = [entry for entry in entries if entry["key"] == key]
    if any(entry["outcome"] in AMBIGUOUS_OUTCOMES for entry in prior):
        return _base_result(
            result="RECONCILE_AMBIGUOUS",
            reason="prior typed action outcome is ambiguous; reconcile before any retry",
            watch_result=name,
            next_check=watch_result.get("next_eligible_check_at"),
        )
    if any(entry["outcome"] == "APPLIED" for entry in prior):
        _persist_watch(request["watch_state_path"], watch_result)
        return _base_result(
            result="DEDUP",
            reason="the same typed action was already applied",
            watch_result=name,
            next_check=watch_result.get("next_eligible_check_at"),
        )
    if request["verification"] is None:
        return _base_result(
            result="AUTHORITY_DENIED",
            reason="trusted verification assertions are missing; caller-supplied permission or dispatch facts are not authority",
            watch_result=name,
            next_check=watch_result.get("next_eligible_check_at"),
        )
    if name == "NOTIFY_OWNER":
        gate = _deliver_notice(request, watch_result)
        if gate == "REPLAY":
            _persist_watch(request["watch_state_path"], watch_result)
            return _base_result(
                result="DEDUP",
                reason="trusted dispatch was already consumed for this owner notice",
                watch_result=name,
                delivery="DEDUP",
                next_check=watch_result.get("next_eligible_check_at"),
            )
        if gate != "ALLOW":
            return _base_result(
                result="AUTHORITY_DENIED",
                reason=gate,
                watch_result=name,
                delivery="DENIED",
                next_check=watch_result.get("next_eligible_check_at"),
            )
        entries.append({"key": key, "outcome": "APPLIED", "result": name})
        _persist_ledger(request["ledger_path"], entries)
        _persist_watch(request["watch_state_path"], watch_result)
        counts = _counts(name)
        return _base_result(
            result="DELIVERED",
            reason="verified INFO owner notice accepted for this exact watch transition",
            watch_result=name,
            actions=counts["actions"],
            wakes=counts["wakes"],
            resumes=counts["resumes"],
            notifications=counts["notifications"],
            level="INFO",
            delivery="VERIFIED",
            next_check=watch_result.get("next_eligible_check_at"),
        )
    try:
        decision = _deliver_write(request, authoritative, watch_result)
    except AdapterFactsError as exc:
        return _base_result(
            result="AUTHORITY_DENIED",
            reason=exc.reason,
            watch_result=name,
            delivery="DENIED",
            next_check=watch_result.get("next_eligible_check_at"),
        )
    adapter_result = str(decision["result"])
    if adapter_result == "APPLIED":
        entries.append({"key": key, "outcome": "APPLIED", "result": name})
        _persist_ledger(request["ledger_path"], entries)
        _persist_watch(request["watch_state_path"], watch_result)
        counts = _counts(name)
        return _base_result(
            result="DELIVERED",
            reason="trusted dispatch authorized exactly one typed action; the host did not mutate GitHub",
            watch_result=name,
            actions=counts["actions"],
            wakes=counts["wakes"],
            resumes=counts["resumes"],
            notifications=counts["notifications"],
            next_check=watch_result.get("next_eligible_check_at"),
        )
    if adapter_result == "NO_CHANGE":
        _persist_watch(request["watch_state_path"], watch_result)
        return _base_result(
            result="DEDUP",
            reason="trusted dispatch was already consumed for this typed action",
            watch_result=name,
            next_check=watch_result.get("next_eligible_check_at"),
        )
    if adapter_result == "STALE_WORKER":
        mapped = "STALE_RECONCILE"
        reason = "authoritative packet no longer matches the typed action"
    elif adapter_result == "RESOURCE_BLOCKED":
        mapped = "RESOURCE_BLOCKED"
        reason = "resource or admission blocks the typed action and does not stop unrelated sessions"
    elif adapter_result in {"RECONCILE_AMBIGUOUS", "TRANSIENT_RETRYABLE", "FAILED_SEMANTIC"}:
        entries.append({"key": key, "outcome": "AMBIGUOUS", "result": name})
        _persist_ledger(request["ledger_path"], entries)
        mapped = "RECONCILE_AMBIGUOUS"
        reason = "typed action outcome is not retryable inside this run"
    else:
        mapped = "AUTHORITY_DENIED"
        reason = str(decision.get("reason") or "trusted dispatch denied the typed action")
    return _base_result(
        result=mapped,
        reason=reason,
        watch_result=name,
        delivery="DENIED" if mapped == "AUTHORITY_DENIED" else "NONE",
        next_check=watch_result.get("next_eligible_check_at"),
    )


def run_once(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile one declared watch. The same files and ledger yield the same delivery count."""
    request = _parse_request(_require_mapping(request_payload, "request"))
    lock = _FileLock(request["lock_path"])
    if not lock.acquire():
        return _base_result(
            result="LOCK_HELD",
            reason="watch host lock is already owned; this run yields without evaluating",
            lock="HELD",
        )
    try:
        state = _load_watch_state(request["watch_state_path"])
        collected_raw, collected = _load_facts(
            request["collected_facts_path"],
            request["watch_class"],
            request["target_repo"],
            request["workstream"],
        )
        watch_result = evaluate(collected_raw, state)
        name = str(watch_result["result"])
        if name not in TYPED_RESULTS:
            _persist_watch(request["watch_state_path"], watch_result)
            if name == "BLOCK_RECONCILIATION":
                mapped = "RECONCILE_AMBIGUOUS"
                reason = "watch reconciliation blocked the action and authorized no retry"
            else:
                mapped = "NO_ACTION"
                reason = "watch result requires no typed external action"
            return _base_result(
                result=mapped,
                reason=reason,
                watch_result=name,
                next_check=watch_result.get("next_eligible_check_at"),
            )
        _authoritative_raw, authoritative = _load_facts(
            request["authoritative_facts_path"],
            request["watch_class"],
            request["target_repo"],
            request["workstream"],
        )
        blocked = _reconcile(_snapshot(collected), _snapshot(authoritative), name)
        if blocked is not None:
            reasons = {
                "STALE_RECONCILE": "authoritative packet or subject changed before the typed action",
                "RESOURCE_BLOCKED": "resource or admission became blocked before the typed action",
                "RECONCILE_AMBIGUOUS": "authoritative mutation state is ambiguous; no typed action is retried",
            }
            return _base_result(
                result=blocked,
                reason=reasons[blocked],
                watch_result=name,
                next_check=watch_result.get("next_eligible_check_at"),
            )
        entries = _ledger_entries(request["ledger_path"])
        return _finish_typed(request, watch_result, authoritative, entries)
    finally:
        lock.release()


def format_report(result: dict[str, Any]) -> str:
    next_check = result["next_eligible_check_at"] if result["next_eligible_check_at"] else "NONE"
    fields = {
        "RESULT": result["result"],
        "EXIT_CODE": "0",
        "REASON": result["reason"],
        "WATCH_RESULT": result["watch_result"],
        "ACTIONS_DELIVERED": str(result["actions_delivered"]),
        "WAKES": str(result["wakes"]),
        "RESUMES": str(result["resumes"]),
        "NOTIFICATIONS": str(result["notifications"]),
        "NOTIFICATION_LEVEL": result["notification_level"],
        "NOTIFICATION_DELIVERY": result["notification_delivery"],
        "LOCK": result["lock"],
        "NEXT_ELIGIBLE_CHECK_AT": next_check,
        "STOPS_UNRELATED_SESSIONS": "NO",
        "MUTATES_EXISTING_SESSIONS": "NO",
        "SPAWNS_PROCESS": "NO",
        "MUTATES_GITHUB": "NO",
        "EXECUTES_COMMAND": "NO",
        "BUSY_LOOP": "NO",
        "SLEEPS": "NO",
        "CADENCE": "EXTERNAL",
    }
    return "\n".join(f"{key}={fields[key]}" for key in REPORT_KEYS) + "\n"


def failure_report(reason: str, deny_class: str = "AMBIGUOUS_FACTS") -> tuple[str, int]:
    lines = [
        "RESULT=REJECT_FACTS",
        "EXIT_CODE=3",
        f"REASON={reason.replace(chr(10), ' ').strip()}",
        f"DENY_CLASS={deny_class}",
        "ACTIONS_DELIVERED=0",
        "WAKES=0",
        "RESUMES=0",
        "NOTIFICATIONS=0",
        "STOPS_UNRELATED_SESSIONS=NO",
        "MUTATES_EXISTING_SESSIONS=NO",
        "SPAWNS_PROCESS=NO",
        "MUTATES_GITHUB=NO",
        "EXECUTES_COMMAND=NO",
        "BUSY_LOOP=NO",
        "SLEEPS=NO",
    ]
    return "\n".join(lines) + "\n", 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run-once",), help="Reconcile one watch and deliver at most one typed action")
    parser.add_argument("--request", required=True, help="JSON file describing one locked run-once watch pass")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_json_file(Path(args.request))
        result = run_once(payload)
        text, code = format_report(result), 0
    except HostRequestError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    except WatchFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    except PlannerFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
