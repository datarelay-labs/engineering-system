#!/usr/bin/env python3
"""Pure coordinator watch / re-entry evaluator.

Facts plus durable watch state in, one bounded re-entry result out. This
tool calls the pure planner and does not launch workers, call GitHub, merge,
send notifications, start or stop sessions, or schedule a busy loop.
Notification output is intent only. Host scheduling and delivery stay outside
this evaluator.

Command:
  evaluate   Emit exactly one re-entry result for one watch

Exit status:
  0  a re-entry result was emitted
  3  facts or watch state were missing, malformed, or contained an execution key
     (DENY_CLASS=EXECUTION_FORBIDDEN when an execution key is present)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from coordinator import (
    PlannerFactsError,
    TOP_LEVEL_KEYS,
    normalize_facts,
    plan,
    reject_execution_keys,
    subject_version,
)

WATCH_CLASSES = frozenset(
    {
        "work_packet_state",
        "exact_head_ci",
        "review_state",
        "worker_progress_or_yield",
        "resource_admission",
    }
)
RESULTS = frozenset(
    {
        "NO_CHANGE",
        "RECHECK_LATER",
        "WAKE_COORDINATOR",
        "RESUME_ADMITTED_WORKER",
        "NOTIFY_OWNER",
        "CLOSE_WATCH",
        "BLOCK_RECONCILIATION",
    }
)
WAIT_DECISIONS = frozenset({"WAIT_EXTERNAL", "WAIT_EXACT_HEAD_CI"})
WAKE_DECISIONS = frozenset(
    {
        "ADMIT_IMPLEMENTATION",
        "AUDIT_DIRTY_TREE",
        "AUTHORIZE_PUBLICATION",
        "AUDIT_REVIEW",
        "MERGE_READY",
        "REPLAN_SEMANTIC_FAILURE",
        "STALE_WORKER",
    }
)
TERMINAL_STATES = frozenset({"OPEN", "CLOSED"})
IDENTITY_KEYS = (
    "target_repo",
    "workstream",
    "intent_revision",
    "watch_class",
    "subject_version",
)
STATE_KEYS = frozenset(
    {
        "schema_version",
        "target_repo",
        "workstream",
        "intent_revision",
        "watch_class",
        "subject_version",
        "last_observation_digest",
        "last_decision",
        "last_decision_key",
        "last_transition_at",
        "consecutive_transient_failures",
        "next_eligible_check_at",
        "terminal_state",
    }
)
WATCH_KEYS = frozenset({"watch_class", "subject_version", "observed_at", "intent_revision"})
BASE_RECHECK_SECONDS = 300
MAX_RECHECK_SECONDS = 3600
REPORT_KEYS = (
    "RESULT",
    "EXIT_CODE",
    "REASON",
    "COORDINATOR_DECISION",
    "WATCH_CLASS",
    "NOTIFICATION",
    "NOTIFICATION_KEY",
    "NOTIFICATION_SUPPRESS_REASON",
    "RESUMES_WORKER",
    "WAKES_COORDINATOR",
    "CLOSES_WATCH",
    "RETRIES_ACTION",
    "STOPS_UNRELATED_SESSIONS",
    "MUTATES_EXISTING_SESSIONS",
    "SPAWNS_PROCESS",
    "MUTATES_GITHUB",
    "SENDS_NOTIFICATION",
    "NEXT_ELIGIBLE_CHECK_AT",
    "NEXT_WATCH_STATE",
)


class WatchFactsError(Exception):
    def __init__(self, reason: str, deny_class: str = "AMBIGUOUS_FACTS"):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WatchFactsError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WatchFactsError(f"{label} must be a non-empty string")
    return value.strip()


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise WatchFactsError(f"{label} contains unknown key {unknown[0]!r}")


def _parse_time(value: str, label: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WatchFactsError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WatchFactsError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WatchFactsError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WatchFactsError(f"malformed JSON in {path}: {exc}") from exc


def _parse_watch(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "watch")
    _reject_unknown(data, WATCH_KEYS, "watch")
    watch_class = _require_str(data.get("watch_class"), "watch.watch_class")
    if watch_class not in WATCH_CLASSES:
        raise WatchFactsError(f"watch.watch_class is unknown: {watch_class}")
    observed_at = _require_str(data.get("observed_at"), "watch.observed_at")
    _parse_time(observed_at, "watch.observed_at")
    subject = None
    if "subject_version" in data:
        subject = _require_str(data.get("subject_version"), "watch.subject_version")
    bound_revision = None
    if "intent_revision" in data:
        revision = data.get("intent_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise WatchFactsError("watch.intent_revision must be an integer >= 1")
        bound_revision = revision
    return {
        "watch_class": watch_class,
        "observed_at": _format_time(_parse_time(observed_at, "watch.observed_at")),
        "subject_version": subject,
        "intent_revision": bound_revision,
    }


def _parse_state(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "watch_state")
    reject_execution_keys(data, path="watch_state")
    _reject_unknown(data, STATE_KEYS, "watch_state")
    if "schema_version" in data and data.get("schema_version") != 1:
        raise WatchFactsError("watch_state.schema_version must be 1")
    present = [key for key in IDENTITY_KEYS if key in data]
    if present and len(present) != len(IDENTITY_KEYS):
        raise WatchFactsError("watch_state identity must include every identity field")
    identity = None
    if present:
        watch_class = _require_str(data.get("watch_class"), "watch_state.watch_class")
        if watch_class not in WATCH_CLASSES:
            raise WatchFactsError(f"watch_state.watch_class is unknown: {watch_class}")
        revision = data.get("intent_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise WatchFactsError("watch_state.intent_revision must be an integer >= 1")
        identity = {
            "target_repo": _require_str(data.get("target_repo"), "watch_state.target_repo"),
            "workstream": _require_str(data.get("workstream"), "watch_state.workstream"),
            "intent_revision": revision,
            "watch_class": watch_class,
            "subject_version": _require_str(data.get("subject_version"), "watch_state.subject_version"),
        }
    terminal = "OPEN"
    if "terminal_state" in data:
        terminal = _require_str(data.get("terminal_state"), "watch_state.terminal_state")
        if terminal not in TERMINAL_STATES:
            raise WatchFactsError(f"watch_state.terminal_state is unknown: {terminal}")
    failures = 0
    if "consecutive_transient_failures" in data:
        failures = data.get("consecutive_transient_failures")
        if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
            raise WatchFactsError("watch_state.consecutive_transient_failures must be an integer >= 0")
    for label in ("last_transition_at", "next_eligible_check_at"):
        if label in data and data.get(label) not in (None, ""):
            _parse_time(_require_str(data.get(label), f"watch_state.{label}"), f"watch_state.{label}")
    return {
        "identity": identity,
        "last_observation_digest": str(data.get("last_observation_digest") or ""),
        "last_decision": str(data.get("last_decision") or ""),
        "last_decision_key": str(data.get("last_decision_key") or ""),
        "last_transition_at": data.get("last_transition_at") or "",
        "consecutive_transient_failures": failures,
        "next_eligible_check_at": data.get("next_eligible_check_at"),
        "terminal_state": terminal,
    }


def _observation_digest(facts: dict[str, Any], decision_name: str) -> str:
    review = facts["review"]
    material = {
        "admission": facts["admission"],
        "ci": facts["ci"],
        "decision": decision_name,
        "intent_revision": facts["packet"]["intent_revision"],
        "mutation_ambiguous": facts["mutation"]["ambiguous"],
        "packet_status": facts["packet"]["status"],
        "pr_head": facts["pr"]["head"],
        "resource": facts["resource"],
        "review_actionable_open": review["actionable_open"],
        "review_state": review["state"],
        "review_subject_head": review["subject_head"],
        "worker": facts["worker"],
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _notification_key(facts: dict[str, Any], result: str, subject: str) -> str:
    packet = facts["packet"]
    return "|".join(
        (
            packet["repository"],
            packet["workstream"],
            str(packet["intent_revision"]),
            result,
            subject,
        )
    )


def _resume_allowed(facts: dict[str, Any]) -> bool:
    worker = facts["worker"]
    packet = facts["packet"]
    return bool(
        worker["present"]
        and worker["progress_evidence"] is True
        and worker["starting_intent_revision"] == packet["intent_revision"]
        and facts["resource"]["result"] in {"PASS", "WARN"}
        and facts["admission"]["decision"] == "ALLOW"
    )


def _backoff_seconds(state: dict[str, Any], unchanged: bool) -> int:
    if not unchanged:
        return BASE_RECHECK_SECONDS
    previous_next = state.get("next_eligible_check_at")
    previous_at = state.get("last_transition_at") or ""
    if not previous_next or not previous_at:
        return BASE_RECHECK_SECONDS
    previous = int((_parse_time(str(previous_next), "watch_state.next_eligible_check_at") - _parse_time(previous_at, "watch_state.last_transition_at")).total_seconds())
    if previous <= 0:
        return BASE_RECHECK_SECONDS
    return min(previous * 2, MAX_RECHECK_SECONDS)


def _next_check(observed_at: str, state: dict[str, Any], unchanged: bool) -> str:
    observed = _parse_time(observed_at, "watch.observed_at")
    return _format_time(observed + timedelta(seconds=_backoff_seconds(state, unchanged)))


def _transient_count(facts: dict[str, Any], state: dict[str, Any]) -> int:
    if facts["failure"]["class"] == "TRANSIENT":
        return int(state["consecutive_transient_failures"]) + 1
    return 0


def _persist(
    facts: dict[str, Any],
    watch: dict[str, Any],
    state: dict[str, Any],
    *,
    subject: str,
    result: str,
    coordinator_decision: str,
    digest: str,
    notification_key: str,
    terminal_state: str,
    next_eligible: str | None,
    unchanged: bool,
) -> dict[str, Any]:
    transition_at = state["last_transition_at"] if unchanged and state["last_transition_at"] else watch["observed_at"]
    if result != "NO_CHANGE":
        transition_at = watch["observed_at"]
    return {
        "schema_version": 1,
        "target_repo": facts["packet"]["repository"],
        "workstream": facts["packet"]["workstream"],
        "intent_revision": facts["packet"]["intent_revision"],
        "watch_class": watch["watch_class"],
        "subject_version": subject,
        "last_observation_digest": digest,
        "last_decision": coordinator_decision,
        "last_decision_key": notification_key,
        "last_transition_at": transition_at,
        "consecutive_transient_failures": _transient_count(facts, state),
        "next_eligible_check_at": next_eligible,
        "terminal_state": terminal_state,
    }


def _emit(
    facts: dict[str, Any],
    watch: dict[str, Any],
    state: dict[str, Any],
    *,
    result: str,
    reason: str,
    coordinator_decision: str,
    subject: str,
    digest: str,
    suppress: str,
    notification: str,
    terminal_state: str = "OPEN",
    next_eligible: str | None = None,
    unchanged: bool = False,
    identity_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if result not in RESULTS:
        raise WatchFactsError(f"internal re-entry result is unknown: {result}")
    key = _notification_key(facts, result, subject)
    if result == "NO_CHANGE" and state["last_decision_key"]:
        key = state["last_decision_key"]
    persisted = _persist(
        facts,
        watch,
        state,
        subject=subject,
        result=result,
        coordinator_decision=coordinator_decision,
        digest=digest,
        notification_key=key,
        terminal_state=terminal_state,
        next_eligible=next_eligible,
        unchanged=unchanged,
    )
    if identity_override is not None:
        persisted.update(identity_override)
    return {
        "schema_version": 1,
        "result": result,
        "reason": reason,
        "coordinator_decision": coordinator_decision,
        "watch_class": watch["watch_class"],
        "notification_disposition": notification,
        "notification_key": key,
        "notification_suppress_reason": suppress,
        "resumes_worker": result == "RESUME_ADMITTED_WORKER",
        "wakes_coordinator": result == "WAKE_COORDINATOR",
        "closes_watch": result == "CLOSE_WATCH",
        "retries_action": False,
        "stops_unrelated_sessions": False,
        "mutates_existing_sessions": False,
        "spawns_process": False,
        "mutates_github": False,
        "sends_notification": False,
        "next_eligible_check_at": next_eligible,
        "next_watch_state": persisted,
        "repository": facts["packet"]["repository"],
        "workstream": facts["packet"]["workstream"],
        "intent_revision": facts["packet"]["intent_revision"],
    }


def _close_stale(
    facts: dict[str, Any],
    watch: dict[str, Any],
    state: dict[str, Any],
    *,
    subject: str,
    reason: str,
) -> dict[str, Any]:
    identity = state["identity"] or {
        "target_repo": facts["packet"]["repository"],
        "workstream": facts["packet"]["workstream"],
        "intent_revision": watch["intent_revision"] or facts["packet"]["intent_revision"],
        "watch_class": watch["watch_class"],
        "subject_version": subject,
    }
    emitted = _emit(
        facts,
        watch,
        state,
        result="CLOSE_WATCH",
        reason=reason,
        coordinator_decision="STALE_WATCH",
        subject=identity["subject_version"],
        digest="STALE",
        suppress="NONE",
        notification="SEND",
        terminal_state="CLOSED",
        next_eligible=None,
        identity_override={
            "target_repo": identity["target_repo"],
            "workstream": identity["workstream"],
            "intent_revision": identity["intent_revision"],
            "watch_class": identity["watch_class"],
            "subject_version": identity["subject_version"],
            "terminal_state": "CLOSED",
            "next_eligible_check_at": None,
        },
    )
    emitted["resumes_worker"] = False
    emitted["wakes_coordinator"] = False
    return emitted


def _map_decision(
    facts: dict[str, Any],
    watch: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
    subject: str,
) -> dict[str, Any]:
    name = decision["decision"]
    digest = _observation_digest(facts, name)
    unchanged = bool(state["last_observation_digest"]) and state["last_observation_digest"] == digest
    same_decision = bool(state["last_decision"]) and state["last_decision"] == name

    if name == "NOOP_COMPLETE" or facts["packet"]["status"] == "COMPLETE":
        return _emit(
            facts,
            watch,
            state,
            result="CLOSE_WATCH",
            reason="COMPLETE packet closes the watch",
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress="NONE",
            notification="SEND",
            terminal_state="CLOSED",
            next_eligible=None,
        )
    if name == "RECONCILE_AMBIGUOUS":
        notify_key = _notification_key(facts, "BLOCK_RECONCILIATION", subject)
        duplicate = state["last_decision_key"] == notify_key
        return _emit(
            facts,
            watch,
            state,
            result="BLOCK_RECONCILIATION",
            reason="ambiguous external mutation must be reconciled before any retry",
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress="DEDUP" if duplicate else "NONE",
            notification="SUPPRESS" if duplicate else "SEND",
            next_eligible=None,
        )
    if name == "BLOCK_HUMAN":
        notify_key = _notification_key(facts, "NOTIFY_OWNER", subject)
        if state["last_decision_key"] == notify_key:
            return _emit(
                facts,
                watch,
                state,
                result="NO_CHANGE",
                reason="identical owner notification is deduplicated",
                coordinator_decision=name,
                subject=subject,
                digest=digest,
                suppress="DEDUP",
                notification="SUPPRESS",
                next_eligible=_next_check(watch["observed_at"], state, True),
                unchanged=True,
            )
        return _emit(
            facts,
            watch,
            state,
            result="NOTIFY_OWNER",
            reason="human action is required and is not auto-authorized",
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress="NONE",
            notification="SEND",
            next_eligible=None,
        )
    if name == "RESUME_WORKER":
        if not _resume_allowed(facts):
            return _emit(
                facts,
                watch,
                state,
                result="RECHECK_LATER",
                reason="resource or admission facts block worker resume and do not mutate unrelated sessions",
                coordinator_decision=name,
                subject=subject,
                digest=digest,
                suppress="IDENTICAL_WAIT" if unchanged else "QUIET_WAIT",
                notification="SUPPRESS",
                next_eligible=_next_check(watch["observed_at"], state, unchanged),
                unchanged=unchanged,
            )
        if same_decision and unchanged:
            return _emit(
                facts,
                watch,
                state,
                result="NO_CHANGE",
                reason="admitted worker resume was already emitted for this observation",
                coordinator_decision=name,
                subject=subject,
                digest=digest,
                suppress="DEDUP",
                notification="SUPPRESS",
                next_eligible=_next_check(watch["observed_at"], state, True),
                unchanged=True,
            )
        return _emit(
            facts,
            watch,
            state,
            result="RESUME_ADMITTED_WORKER",
            reason="admitted same-revision worker has positive progress and resource admission still allows resume",
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress="NONE",
            notification="SUPPRESS",
            next_eligible=_next_check(watch["observed_at"], state, False),
        )
    if name == "YIELD_RESOURCE" or name in WAIT_DECISIONS or name == "NOOP_PAUSED":
        reason = "identical observation schedules a bounded recheck without notification"
        suppress = "IDENTICAL_WAIT" if unchanged else "QUIET_WAIT"
        if name == "YIELD_RESOURCE":
            reason = "resource block does not resume a worker and does not mutate unrelated sessions"
        elif name == "NOOP_PAUSED":
            reason = "PAUSED packet does not launch or resume a worker"
        elif "progress evidence" in decision["reason"]:
            reason = "worker liveness without positive progress does not resume"
        return _emit(
            facts,
            watch,
            state,
            result="RECHECK_LATER",
            reason=reason,
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress=suppress,
            notification="SUPPRESS",
            next_eligible=_next_check(watch["observed_at"], state, unchanged),
            unchanged=unchanged,
        )
    if name in WAKE_DECISIONS:
        if same_decision:
            return _emit(
                facts,
                watch,
                state,
                result="NO_CHANGE",
                reason="coordinator decision is unchanged, so the watch does not wake again",
                coordinator_decision=name,
                subject=subject,
                digest=digest,
                suppress="DEDUP",
                notification="SUPPRESS",
                next_eligible=_next_check(watch["observed_at"], state, True),
                unchanged=True,
            )
        return _emit(
            facts,
            watch,
            state,
            result="WAKE_COORDINATOR",
            reason=f"coordinator decision changed to {name}",
            coordinator_decision=name,
            subject=subject,
            digest=digest,
            suppress="NONE",
            notification="SEND",
            next_eligible=_next_check(watch["observed_at"], state, False),
        )
    raise WatchFactsError(f"coordinator decision is not mapped: {name}")


def evaluate(facts_payload: dict[str, Any], state_payload: dict[str, Any]) -> dict[str, Any]:
    """Return one watch re-entry result.

    The same facts and watch state always yield the same result. The
    evaluator never treats a stale intent revision, unbound CI head, missing
    progress evidence, or resource block as permission to resume or merge.
    """
    raw = _require_mapping(facts_payload, "facts")
    reject_execution_keys(raw)
    _reject_unknown(raw, TOP_LEVEL_KEYS | {"watch"}, "facts")
    if "watch" not in raw:
        raise WatchFactsError("facts.watch is required")
    watch = _parse_watch(raw["watch"])
    state = _parse_state(state_payload)
    planner_payload = {key: value for key, value in raw.items() if key != "watch"}
    try:
        facts = normalize_facts(planner_payload)
    except PlannerFactsError:
        raise
    packet = facts["packet"]
    subject = watch["subject_version"] or subject_version(facts)
    identity = state["identity"]
    if identity is not None:
        if identity["target_repo"] != packet["repository"] or identity["workstream"] != packet["workstream"]:
            raise WatchFactsError("watch_state identity does not match the packet repository or workstream")
        if identity["watch_class"] != watch["watch_class"]:
            raise WatchFactsError("watch_state.watch_class does not match facts.watch.watch_class")
    if state["terminal_state"] == "CLOSED":
        closed_identity = identity or {
            "target_repo": packet["repository"],
            "workstream": packet["workstream"],
            "intent_revision": packet["intent_revision"],
            "watch_class": watch["watch_class"],
            "subject_version": subject,
        }
        return _emit(
            facts,
            watch,
            state,
            result="CLOSE_WATCH",
            reason="watch is already closed",
            coordinator_decision="CLOSED",
            subject=closed_identity["subject_version"],
            digest=state["last_observation_digest"] or "CLOSED",
            suppress="DEDUP",
            notification="SUPPRESS",
            terminal_state="CLOSED",
            next_eligible=None,
            unchanged=True,
            identity_override={
                "target_repo": closed_identity["target_repo"],
                "workstream": closed_identity["workstream"],
                "intent_revision": closed_identity["intent_revision"],
                "watch_class": closed_identity["watch_class"],
                "subject_version": closed_identity["subject_version"],
                "terminal_state": "CLOSED",
                "next_eligible_check_at": None,
            },
        )
    stale_revision = (identity is not None and identity["intent_revision"] != packet["intent_revision"]) or (
        watch["intent_revision"] is not None and watch["intent_revision"] != packet["intent_revision"]
    )
    stale_subject = identity is not None and identity["subject_version"] != subject
    if stale_revision or stale_subject:
        return _close_stale(
            facts,
            watch,
            state,
            subject=subject,
            reason="stale watch identity cannot act and must be reconciled against the current packet",
        )
    if facts["mutation"]["ambiguous"] or facts["failure"]["class"] == "AMBIGUOUS_MUTATION":
        return _map_decision(
            facts,
            watch,
            state,
            {"decision": "RECONCILE_AMBIGUOUS", "reason": "ambiguous mutation"},
            subject,
        )
    decision = plan(planner_payload)
    return _map_decision(facts, watch, state, decision, subject)


def format_report(result: dict[str, Any], *, exit_code: int = 0, deny_class: str | None = None) -> str:
    next_check = result["next_eligible_check_at"] if result["next_eligible_check_at"] else "NONE"
    fields = {
        "RESULT": result["result"],
        "EXIT_CODE": str(exit_code),
        "REASON": result["reason"],
        "COORDINATOR_DECISION": result["coordinator_decision"],
        "WATCH_CLASS": result["watch_class"],
        "NOTIFICATION": result["notification_disposition"],
        "NOTIFICATION_KEY": result["notification_key"],
        "NOTIFICATION_SUPPRESS_REASON": result["notification_suppress_reason"],
        "RESUMES_WORKER": "YES" if result["resumes_worker"] else "NO",
        "WAKES_COORDINATOR": "YES" if result["wakes_coordinator"] else "NO",
        "CLOSES_WATCH": "YES" if result["closes_watch"] else "NO",
        "RETRIES_ACTION": "NO",
        "STOPS_UNRELATED_SESSIONS": "NO",
        "MUTATES_EXISTING_SESSIONS": "NO",
        "SPAWNS_PROCESS": "NO",
        "MUTATES_GITHUB": "NO",
        "SENDS_NOTIFICATION": "NO",
        "NEXT_ELIGIBLE_CHECK_AT": next_check,
        "NEXT_WATCH_STATE": json.dumps(result["next_watch_state"], sort_keys=True, separators=(",", ":")),
    }
    lines = [f"{key}={fields[key]}" for key in REPORT_KEYS]
    if deny_class:
        lines.append(f"DENY_CLASS={deny_class}")
    return "\n".join(lines) + "\n"


def failure_report(reason: str, deny_class: str = "AMBIGUOUS_FACTS") -> tuple[str, int]:
    lines = [
        "RESULT=REJECT_FACTS",
        "EXIT_CODE=3",
        f"REASON={reason.replace(chr(10), ' ').strip()}",
        f"DENY_CLASS={deny_class}",
        "RESUMES_WORKER=NO",
        "WAKES_COORDINATOR=NO",
        "CLOSES_WATCH=NO",
        "RETRIES_ACTION=NO",
        "STOPS_UNRELATED_SESSIONS=NO",
        "MUTATES_EXISTING_SESSIONS=NO",
        "SPAWNS_PROCESS=NO",
        "MUTATES_GITHUB=NO",
        "SENDS_NOTIFICATION=NO",
    ]
    return "\n".join(lines) + "\n", 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate",), help="Emit one re-entry result from structured facts")
    parser.add_argument("--facts", required=True, help="JSON file of machine-readable coordinator and watch facts")
    parser.add_argument("--watch-state", required=True, help="JSON file of bounded durable watch state")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = evaluate(load_json_file(Path(args.facts)), load_json_file(Path(args.watch_state)))
        text, code = format_report(result), 0
    except WatchFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    except PlannerFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    # Fact evaluation only; never spawn, mutate GitHub, merge, notify, or stop sessions.
    raise SystemExit(main())
