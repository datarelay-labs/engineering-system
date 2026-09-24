#!/usr/bin/env python3
"""Deterministic parallel-work admission, WIP ownership, and handoff sizing.

Coordinator-facing oracle for whether a second worker may start. Decisions are
pure functions over packet/claim/worktree/resource facts. This tool never
stops, kills, attaches to, or otherwise mutates existing Cursor sessions.

Commands:
  admit   ALLOW/DENY starting a proposed worker claim
  size    BATCH/KEEP/SPLIT handoff sizing from structured signals
  release ALLOW/DENY claim release or worktree cleanup reconciliation

Exit status:
  0  ALLOW (or sizing decision emitted)
  2  DENY for an explicit policy reason
  3  BLOCK because facts were missing, malformed, or ambiguous
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
from pathlib import Path
from typing import Any

ACTIVE_CLAIM_STATUSES = frozenset(
    {"CLAIMED", "ACTIVE", "WAIT", "YIELD", "RETRY"}
)
RELEASEABLE_CLAIM_STATUSES = frozenset(
    {"COMPLETE", "ABANDON", "SUPERSEDED", "RELEASED"}
)
KNOWN_CLAIM_STATUSES = ACTIVE_CLAIM_STATUSES | RELEASEABLE_CLAIM_STATUSES
HOST_OK = frozenset({"PASS", "WARN"})
EFFORT_BANDS = frozenset({"micro", "keep", "oversize"})
SIZING_DECISIONS = frozenset({"BATCH", "KEEP", "SPLIT"})

REPORT_KEYS_ADMIT = (
    "DECISION",
    "EXIT_CODE",
    "REASON",
    "DENY_CLASS",
    "PROPOSED_CLAIM_ID",
    "ACTIVE_CLAIM_COUNT",
    "WIP_LIMIT",
    "HOST_RESOURCE_RESULT",
    "MUTATES_EXISTING_SESSIONS",
)
REPORT_KEYS_SIZE = (
    "DECISION",
    "EXIT_CODE",
    "REASON",
    "WORK_PACKET_SIZING",
    "PRIMARY_OUTCOME_COUNT",
    "EFFORT_BAND",
    "CONTEXT_BUDGET_OK",
)
REPORT_KEYS_RELEASE = (
    "DECISION",
    "EXIT_CODE",
    "REASON",
    "DENY_CLASS",
    "ACTION",
    "CLAIM_ID",
    "MUTATES_EXISTING_SESSIONS",
)


class AdmissionFactsError(Exception):
    def __init__(self, reason: str, deny_class: str = "AMBIGUOUS_FACTS"):
        super().__init__(reason)
        self.reason = reason
        self.deny_class = deny_class


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdmissionFactsError(f"{label} must be a JSON object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdmissionFactsError(f"{label} must be a non-empty string")
    return value.strip()


def _require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdmissionFactsError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise AdmissionFactsError(f"{label} must be >= {minimum}")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise AdmissionFactsError(f"{label} must be a boolean")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AdmissionFactsError(f"{label} must be a JSON array")
    return value


def normalize_path(value: str) -> str:
    """Canonicalize a repository-relative owned path.

    Collapses ``.`` / ``..`` lexically, rejects absolute paths and root escapes,
    and fails closed on empty results so alias/traversal forms cannot bypass
    overlap detection.
    """
    text = value.strip().replace("\\", "/")
    if not text or text in {".", "/"}:
        raise AdmissionFactsError("owned path must be a non-empty repository-relative path")
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise AdmissionFactsError("owned path must be repository-relative, not absolute")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise AdmissionFactsError("owned path escapes repository root via '..'")
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise AdmissionFactsError("owned path resolves to an empty path")
    return "/".join(parts)


def canonicalize_worktree(value: str) -> str:
    """Lexically canonicalize an absolute worktree path without filesystem I/O."""
    text = value.strip().replace("\\", "/")
    if not text.startswith("/"):
        raise AdmissionFactsError("worktree must be an absolute path")
    # posixpath.normpath collapses /tmp/x/../wt -> /tmp/wt without resolving symlinks.
    normalized = posixpath.normpath(text)
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if normalized in {"", ".", "/"}:
        raise AdmissionFactsError("worktree canonicalization produced an invalid path")
    return normalized


def paths_overlap(left: str, right: str) -> bool:
    a = normalize_path(left)
    b = normalize_path(right)
    if a == b:
        return True
    a_prefix = a if a.endswith("/") else a + "/"
    b_prefix = b if b.endswith("/") else b + "/"
    return a.startswith(b_prefix) or b.startswith(a_prefix)


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdmissionFactsError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdmissionFactsError(f"malformed JSON in {path}: {exc}") from exc


def parse_claim(raw: Any, *, label: str) -> dict[str, Any]:
    data = _require_mapping(raw, label)
    claim_id = _require_str(data.get("claim_id"), f"{label}.claim_id")
    repository = _require_str(data.get("repository"), f"{label}.repository")
    workstream = _require_str(data.get("workstream"), f"{label}.workstream")
    intent_revision = _require_int(
        data.get("intent_revision"), f"{label}.intent_revision", minimum=1
    )
    worktree = canonicalize_worktree(
        _require_str(data.get("worktree"), f"{label}.worktree")
    )
    owned_paths_raw = _require_list(data.get("owned_paths", []), f"{label}.owned_paths")
    owned_paths: list[str] = []
    for index, item in enumerate(owned_paths_raw):
        owned_paths.append(
            normalize_path(_require_str(item, f"{label}.owned_paths[{index}]"))
        )
    status = _require_str(data.get("status", "CLAIMED"), f"{label}.status").upper()
    if status not in KNOWN_CLAIM_STATUSES:
        raise AdmissionFactsError(f"{label}.status is unknown: {status}")
    shared_runtime = data.get("shared_runtime")
    runtime: dict[str, Any] | None = None
    if shared_runtime is not None:
        runtime_map = _require_mapping(shared_runtime, f"{label}.shared_runtime")
        runtime = {
            "id": _require_str(runtime_map.get("id"), f"{label}.shared_runtime.id"),
            "isolated": _require_bool(
                runtime_map.get("isolated"), f"{label}.shared_runtime.isolated"
            ),
        }
    return {
        "claim_id": claim_id,
        "repository": repository,
        "workstream": workstream,
        "intent_revision": intent_revision,
        "worktree": worktree,
        "owned_paths": owned_paths,
        "status": status,
        "shared_runtime": runtime,
        "dirty": _require_bool(data.get("dirty", False), f"{label}.dirty"),
        "unpushed": _require_bool(data.get("unpushed", False), f"{label}.unpushed"),
        "ambiguous": _require_bool(data.get("ambiguous", False), f"{label}.ambiguous"),
    }


def parse_host_resource(raw: Any) -> dict[str, Any]:
    data = _require_mapping(raw, "host_resource")
    result = _require_str(data.get("result"), "host_resource.result").upper()
    if result not in {"PASS", "WARN", "BLOCK"}:
        raise AdmissionFactsError("host_resource.result must be PASS, WARN, or BLOCK")
    exit_code = _require_int(data.get("exit_code"), "host_resource.exit_code", minimum=0)
    if result in HOST_OK and exit_code != 0:
        raise AdmissionFactsError("host_resource PASS/WARN requires exit_code 0")
    if result == "BLOCK" and exit_code not in {2, 3}:
        raise AdmissionFactsError("host_resource BLOCK requires exit_code 2 or 3")
    return {"result": result, "exit_code": exit_code}


def active_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if claim["status"] in ACTIVE_CLAIM_STATUSES]


def claim_identity_key(claim: dict[str, Any]) -> tuple[str, str, int]:
    return (claim["repository"], claim["workstream"], claim["intent_revision"])


def deny(reason: str, deny_class: str, **fields: Any) -> dict[str, str]:
    payload = {
        "DECISION": "DENY",
        "EXIT_CODE": "2",
        "REASON": reason,
        "DENY_CLASS": deny_class,
        "MUTATES_EXISTING_SESSIONS": "NO",
    }
    for key, value in fields.items():
        payload[key] = str(value)
    return payload


def allow(reason: str, **fields: Any) -> dict[str, str]:
    payload = {
        "DECISION": "ALLOW",
        "EXIT_CODE": "0",
        "REASON": reason,
        "DENY_CLASS": "NONE",
        "MUTATES_EXISTING_SESSIONS": "NO",
    }
    for key, value in fields.items():
        payload[key] = str(value)
    return payload


def evaluate_admit(request: dict[str, Any]) -> dict[str, str]:
    proposed = parse_claim(request.get("proposed"), label="proposed")
    existing_raw = _require_list(request.get("existing_claims", []), "existing_claims")
    existing = [
        parse_claim(item, label=f"existing_claims[{index}]")
        for index, item in enumerate(existing_raw)
    ]
    host = parse_host_resource(request.get("host_resource"))
    wip_limit = _require_int(request.get("wip_limit", 1), "wip_limit", minimum=1)

    if proposed["ambiguous"]:
        return deny(
            "proposed claim is marked ambiguous",
            "AMBIGUOUS_CLAIM",
            PROPOSED_CLAIM_ID=proposed["claim_id"],
            ACTIVE_CLAIM_COUNT=len(active_claims(existing)),
            WIP_LIMIT=wip_limit,
            HOST_RESOURCE_RESULT=host["result"],
        )
    if proposed["status"] not in ACTIVE_CLAIM_STATUSES:
        return deny(
            f"proposed claim status {proposed['status']} is not an active worker status",
            "AMBIGUOUS_CLAIM",
            PROPOSED_CLAIM_ID=proposed["claim_id"],
            ACTIVE_CLAIM_COUNT=len(active_claims(existing)),
            WIP_LIMIT=wip_limit,
            HOST_RESOURCE_RESULT=host["result"],
        )
    if host["result"] not in HOST_OK:
        return deny(
            f"host resource admission blocked with result {host['result']} exit {host['exit_code']}",
            "HOST_BUDGET",
            PROPOSED_CLAIM_ID=proposed["claim_id"],
            ACTIVE_CLAIM_COUNT=len(active_claims(existing)),
            WIP_LIMIT=wip_limit,
            HOST_RESOURCE_RESULT=host["result"],
        )

    live = active_claims(existing)
    seen_ids: set[str] = set()
    for claim in existing:
        if claim["claim_id"] in seen_ids:
            return deny(
                f"duplicate claim_id {claim['claim_id']} in existing claims",
                "AMBIGUOUS_CLAIM",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )
        seen_ids.add(claim["claim_id"])
        if claim["ambiguous"] and claim["status"] in ACTIVE_CLAIM_STATUSES:
            return deny(
                f"existing claim {claim['claim_id']} is ambiguous",
                "AMBIGUOUS_CLAIM",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )

    if proposed["claim_id"] in seen_ids:
        return deny(
            f"proposed claim_id {proposed['claim_id']} already exists",
            "AMBIGUOUS_CLAIM",
            PROPOSED_CLAIM_ID=proposed["claim_id"],
            ACTIVE_CLAIM_COUNT=len(live),
            WIP_LIMIT=wip_limit,
            HOST_RESOURCE_RESULT=host["result"],
        )

    # Default remains sequential: a second active worker requires explicit room.
    if live and len(live) >= wip_limit:
        return deny(
            f"active claims {len(live)} already meet wip_limit {wip_limit}",
            "WIP_LIMIT",
            PROPOSED_CLAIM_ID=proposed["claim_id"],
            ACTIVE_CLAIM_COUNT=len(live),
            WIP_LIMIT=wip_limit,
            HOST_RESOURCE_RESULT=host["result"],
        )

    for claim in live:
        # Host-wide: shared mutable runtimes conflict across repositories.
        left_rt = proposed["shared_runtime"]
        right_rt = claim["shared_runtime"]
        if (
            left_rt is not None
            and right_rt is not None
            and left_rt["id"] == right_rt["id"]
            and (not left_rt["isolated"] or not right_rt["isolated"])
        ):
            return deny(
                f"shared runtime {left_rt['id']} is not isolated for both claims",
                "SHARED_RUNTIME",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )

        if claim["repository"] != proposed["repository"]:
            continue
        if claim["worktree"] == proposed["worktree"]:
            return deny(
                f"proposed worktree overlaps active claim {claim['claim_id']}",
                "SHARED_WORKTREE",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )
        if claim_identity_key(claim) == claim_identity_key(proposed):
            return deny(
                "repository/workstream/intent_revision claim identity already held",
                "OVERLAPPING_CLAIM",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )
        if (
            claim["workstream"] == proposed["workstream"]
            and claim["intent_revision"] != proposed["intent_revision"]
        ):
            return deny(
                f"stale or conflicting intent_revision against claim {claim['claim_id']}",
                "STALE_INTENT_REVISION",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )
        # Same-repo parallelism requires non-empty ownership facts on both sides.
        if not proposed["owned_paths"] or not claim["owned_paths"]:
            return deny(
                "insufficient ownership facts to prove same-repository independence",
                "INSUFFICIENT_OWNERSHIP",
                PROPOSED_CLAIM_ID=proposed["claim_id"],
                ACTIVE_CLAIM_COUNT=len(live),
                WIP_LIMIT=wip_limit,
                HOST_RESOURCE_RESULT=host["result"],
            )
        for left in proposed["owned_paths"]:
            for right in claim["owned_paths"]:
                if paths_overlap(left, right):
                    return deny(
                        f"owned path {left!r} overlaps claim {claim['claim_id']} path {right!r}",
                        "OVERLAPPING_PATHS",
                        PROPOSED_CLAIM_ID=proposed["claim_id"],
                        ACTIVE_CLAIM_COUNT=len(live),
                        WIP_LIMIT=wip_limit,
                        HOST_RESOURCE_RESULT=host["result"],
                    )

    return allow(
        "proposed worker is independent under claim/worktree/resource facts",
        PROPOSED_CLAIM_ID=proposed["claim_id"],
        ACTIVE_CLAIM_COUNT=len(live),
        WIP_LIMIT=wip_limit,
        HOST_RESOURCE_RESULT=host["result"],
    )


def evaluate_size(request: dict[str, Any]) -> dict[str, str]:
    primary_outcome_count = _require_int(
        request.get("primary_outcome_count"), "primary_outcome_count", minimum=1
    )
    adjacent_share_oracle = _require_bool(
        request.get("adjacent_share_oracle"), "adjacent_share_oracle"
    )
    unrelated_domains = _require_bool(request.get("unrelated_domains"), "unrelated_domains")
    distinct_approval_gates = _require_bool(
        request.get("distinct_approval_gates"), "distinct_approval_gates"
    )
    unclear_rollback = _require_bool(request.get("unclear_rollback"), "unclear_rollback")
    effort_band = _require_str(request.get("effort_band"), "effort_band").lower()
    if effort_band not in EFFORT_BANDS:
        raise AdmissionFactsError("effort_band must be micro, keep, or oversize")
    context_budget_ok = _require_bool(request.get("context_budget_ok"), "context_budget_ok")
    included_issue_count = _require_int(
        request.get("included_issue_count", 1), "included_issue_count", minimum=1
    )

    split_reasons: list[str] = []
    if primary_outcome_count > 1:
        split_reasons.append("multiple primary outcomes")
    if unrelated_domains:
        split_reasons.append("unrelated domains")
    if distinct_approval_gates:
        split_reasons.append("distinct approval gates")
    if unclear_rollback:
        split_reasons.append("unclear rollback boundaries")
    if effort_band == "oversize":
        split_reasons.append("effort_band oversize")
    if not context_budget_ok:
        split_reasons.append("insufficient context budget for implementation plus validation")

    if split_reasons:
        decision = "SPLIT"
        reason = "; ".join(split_reasons)
    elif effort_band == "micro" and adjacent_share_oracle:
        decision = "BATCH"
        reason = "micro findings share subsystem/context/validation oracle"
    else:
        decision = "KEEP"
        reason = "one coherent independently verifiable outcome"
        if effort_band == "micro" and not adjacent_share_oracle:
            reason = "lone micro finding has no adjacent batch partner; keep as one handoff"

    return {
        "DECISION": decision,
        "EXIT_CODE": "0",
        "REASON": reason,
        "WORK_PACKET_SIZING": decision,
        "PRIMARY_OUTCOME_COUNT": str(primary_outcome_count),
        "EFFORT_BAND": effort_band,
        "CONTEXT_BUDGET_OK": "YES" if context_budget_ok else "NO",
        "INCLUDED_ISSUE_COUNT": str(included_issue_count),
        "MUTATES_EXISTING_SESSIONS": "NO",
    }


def evaluate_release(request: dict[str, Any]) -> dict[str, str]:
    action = _require_str(request.get("action"), "action").lower()
    if action not in {"release-claim", "cleanup-worktree"}:
        raise AdmissionFactsError("action must be release-claim or cleanup-worktree")
    claim_id = _require_str(request.get("claim_id"), "claim_id")
    existing_raw = _require_list(request.get("existing_claims", []), "existing_claims")
    existing = [
        parse_claim(item, label=f"existing_claims[{index}]")
        for index, item in enumerate(existing_raw)
    ]
    matches = [claim for claim in existing if claim["claim_id"] == claim_id]
    if not matches:
        return deny(
            f"claim_id {claim_id} not found",
            "AMBIGUOUS_CLAIM",
            ACTION=action,
            CLAIM_ID=claim_id,
        )
    if len(matches) > 1:
        return deny(
            f"claim_id {claim_id} matches multiple records",
            "AMBIGUOUS_CLAIM",
            ACTION=action,
            CLAIM_ID=claim_id,
        )
    claim = matches[0]
    if claim["ambiguous"]:
        return deny(
            f"claim {claim_id} is ambiguous; reconcile before release",
            "AMBIGUOUS_CLAIM",
            ACTION=action,
            CLAIM_ID=claim_id,
        )
    if action == "release-claim":
        if claim["status"] not in RELEASEABLE_CLAIM_STATUSES:
            return deny(
                f"claim {claim_id} status {claim['status']} is not releasable",
                "ACTIVE_CLAIM",
                ACTION=action,
                CLAIM_ID=claim_id,
            )
        return allow(
            f"claim {claim_id} may release ownership without mutating unrelated sessions",
            ACTION=action,
            CLAIM_ID=claim_id,
        )

    # cleanup-worktree: never auto-delete dirty/ambiguous/unpushed state.
    if claim["dirty"] or claim["unpushed"] or claim["ambiguous"]:
        return deny(
            f"refusing worktree cleanup for claim {claim_id} with dirty/unpushed/ambiguous state",
            "UNSAFE_CLEANUP",
            ACTION=action,
            CLAIM_ID=claim_id,
        )
    if claim["status"] not in RELEASEABLE_CLAIM_STATUSES:
        return deny(
            f"claim {claim_id} status {claim['status']} is not cleanup-eligible",
            "ACTIVE_CLAIM",
            ACTION=action,
            CLAIM_ID=claim_id,
        )
    return allow(
        f"claim {claim_id} worktree cleanup is safe under reconciled facts",
        ACTION=action,
        CLAIM_ID=claim_id,
    )


def format_report(fields: dict[str, str], keys: tuple[str, ...]) -> str:
    lines = []
    for key in keys:
        if key not in fields:
            continue
        value = fields[key].replace("\n", " ").strip()
        lines.append(f"{key}={value}")
    for key, value in fields.items():
        if key in keys:
            continue
        lines.append(f"{key}={value.replace(chr(10), ' ').strip()}")
    return "\n".join(lines) + "\n"


def failure_report(reason: str, deny_class: str = "AMBIGUOUS_FACTS") -> tuple[str, int]:
    text = format_report(
        {
            "DECISION": "DENY",
            "EXIT_CODE": "3",
            "REASON": reason,
            "DENY_CLASS": deny_class,
            "MUTATES_EXISTING_SESSIONS": "NO",
        },
        REPORT_KEYS_ADMIT,
    )
    return text, 3


def run_command(command: str, payload: dict[str, Any]) -> tuple[str, int]:
    if command == "admit":
        report = evaluate_admit(payload)
        return format_report(report, REPORT_KEYS_ADMIT), int(report["EXIT_CODE"])
    if command == "size":
        report = evaluate_size(payload)
        return format_report(report, REPORT_KEYS_SIZE), int(report["EXIT_CODE"])
    if command == "release":
        report = evaluate_release(payload)
        return format_report(report, REPORT_KEYS_RELEASE), int(report["EXIT_CODE"])
    raise AdmissionFactsError(f"unknown command {command}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("admit", "size", "release"),
        help="Admission decision, handoff sizing, or claim release/cleanup",
    )
    parser.add_argument(
        "--request-json",
        required=True,
        help="JSON file of machine-readable packet/claim/resource/sizing facts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_json_file(Path(args.request_json))
        if not isinstance(payload, dict):
            raise AdmissionFactsError("request JSON must be an object")
        text, code = run_command(args.command, payload)
    except AdmissionFactsError as exc:
        text, code = failure_report(exc.reason, exc.deny_class)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    # Fact evaluation only; never stop/kill/mutate existing Cursor sessions.
    raise SystemExit(main())
