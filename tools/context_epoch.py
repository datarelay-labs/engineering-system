#!/usr/bin/env python3
"""Bounded Work Packet projection and content-free context-epoch policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from execution_profile import (
    FENCE_RE,
    HEADING_RE,
    META_RE,
    ProfileError,
    load_profile,
    packet_authority,
    scan_packet_metadata,
)
ALLOWED_STATUSES = {"ACTIVE", "PAUSED", "BLOCKED", "COMPLETE"}
SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")
SAFE_TASK_KIND_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,63}$")
SAFE_HOOK_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
REQUIRED_META_COMMON = (
    "PACKET_VERSION", "TARGET_REPO", "WORKSTREAM", "STATUS", "BRANCH",
    "TASK_KIND", "OWNER_INTENT", "INTENT_REVISION", "CHANGE_RISK",
)
META_ORDER = (
    "PACKET_VERSION", "TARGET_REPO", "WORKSTREAM", "STATUS", "BRANCH",
    "TASK_KIND", "OWNER_INTENT", "LAST_VERIFIED_HEAD", "PRIORITY",
    "INTENT_REVISION", "CHANGE_RISK", "EXECUTION_PROFILE",
    "EXECUTION_PROFILE_REVISION", "IMPLEMENTER",
)
CANONICAL_SECTIONS = (
    "Goal",
    "Current State",
    "Next Action",
    "Handoff Sizing",
    "Completion Contract",
    "Follow-up Discoveries",
    "Constraints",
    "Canonical References",
    "Latest Evidence",
    "Blockers",
)
REQUIRED_SECTIONS = ("Goal", "Current State", "Next Action", "Blockers")
PROJECT_SECTIONS = (
    "Goal", "Current State", "Next Action", "Blockers",
    "Handoff Sizing", "Completion Contract", "Constraints",
    "Canonical References", "Latest Evidence",
)
IDENTITY_KEYS = (
    "PACKET_VERSION", "TARGET_REPO", "WORKSTREAM", "STATUS", "BRANCH",
    "TASK_KIND", "INTENT_REVISION", "CHANGE_RISK", "EXECUTION_PROFILE",
    "EXECUTION_PROFILE_REVISION", "IMPLEMENTER",
)
MISSING_IDENTITY_VALUE = "<missing>"
IDENTITY_BINDING_KEYS = IDENTITY_KEYS + ("PACKET_BODY_SHA256",)
DEFAULT_META_VALUE_CAP = 512
DEFAULT_SECTION_CHAR_CAP = 3500
DEFAULT_PROJECTION_CHAR_CAP = 14000
PROJECTION_TAIL_RESERVE = 512
TEMPLATE_META_SENTINELS = {
    "TARGET_REPO": "owner/repository",
    "WORKSTREAM": "replace-with-stable-slug",
    "BRANCH": "replace-with-branch-or-N/A",
    "OWNER_INTENT": "State the owner's current explicit request in one concise line.",
}
TEMPLATE_SECTION_SENTINELS = {
    "Goal": "State the stable workstream outcome in one concise paragraph.",
    "Current State": "- Keep only facts needed to resume now.",
    "Next Action": "State the next bounded **outcome / execution bundle**, not one command, one tiny Issue, or one micro-step.",
}
NATIVE_EVENTS = {
    "sessionStart", "sessionEnd", "beforeSubmitPrompt", "preCompact", "stop",
    "subagentStart", "subagentStop",
}


class ContextError(ValueError):
    """Fail-closed input error."""


@dataclass(frozen=True)
class Packet:
    metadata: dict[str, str]
    sections: dict[str, str]
    duplicate_metadata: tuple[str, ...]
    duplicate_sections: tuple[str, ...]
    headings: tuple[str, ...]
    char_count: int
    line_count: int
    body_sha256: str


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContextError("PACKET_UNREADABLE") from exc


def parse_packet(text: str) -> Packet:
    metadata, duplicate_metadata = scan_packet_metadata(text)
    sections: dict[str, list[str]] = {}
    duplicates: list[str] = []
    headings: list[str] = []
    current: str | None = None
    fence: tuple[str, int] | None = None
    for raw in text.splitlines():
        fence_match = FENCE_RE.match(raw)
        if fence_match:
            marker = fence_match.group(1)
            family = marker[0]
            length = len(marker)
            if fence is None:
                fence = (family, length)
            elif fence[0] == family and length >= fence[1]:
                fence = None
            if current is not None:
                sections[current].append(raw)
            continue
        if fence is not None:
            if current is not None:
                sections[current].append(raw)
            continue
        heading = HEADING_RE.match(raw)
        if heading:
            current = heading.group(1).strip()
            headings.append(current)
            if current in sections:
                duplicates.append(current)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw)
    rendered = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    return Packet(
        metadata,
        rendered,
        tuple(duplicate_metadata),
        tuple(duplicates),
        tuple(headings),
        len(text),
        len(text.splitlines()),
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def analyze_packet(
    packet: Packet,
    *,
    profile_root: Path | str | None = None,
    expected_target_repo: str | None = None,
    warn_chars: int | None = None,
    warn_lines: int | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    blocking: list[str] = []
    compatibility: list[str] = []
    version = packet.metadata.get("PACKET_VERSION")
    if version in {"2", "3"}:
        required = REQUIRED_META_COMMON + (("IMPLEMENTER",) if version == "2" else ("EXECUTION_PROFILE", "EXECUTION_PROFILE_REVISION"))
        missing = [key for key in required if not packet.metadata.get(key)]
        blocking.extend(f"MISSING_META:{key}" for key in missing)
    elif not version:
        blocking.append("MISSING_META:PACKET_VERSION")
    for key in packet.duplicate_metadata:
        blocking.append(f"DUPLICATE_META:{key}")
    if version and version not in {"2", "3"}:
        blocking.append("PACKET_VERSION_INVALID")
    repository = packet.metadata.get("TARGET_REPO")
    if repository and (
        len(repository) > 200 or SAFE_REPO_RE.fullmatch(repository) is None
    ):
        blocking.append("TARGET_REPO_INVALID")
    if expected_target_repo is not None:
        if (
            len(expected_target_repo) > 200
            or SAFE_REPO_RE.fullmatch(expected_target_repo) is None
        ):
            blocking.append("EXPECTED_TARGET_REPO_INVALID")
        elif repository and repository != expected_target_repo:
            blocking.append("TARGET_REPO_SCOPE_MISMATCH")
    workstream = packet.metadata.get("WORKSTREAM")
    if workstream and SAFE_WORKSTREAM_RE.fullmatch(workstream) is None:
        blocking.append("WORKSTREAM_INVALID")
    branch = packet.metadata.get("BRANCH")
    if branch and (
        SAFE_BRANCH_RE.fullmatch(branch) is None
        or ".." in branch
        or branch.endswith("/")
    ):
        blocking.append("BRANCH_INVALID")
    task_kind = packet.metadata.get("TASK_KIND")
    if task_kind and SAFE_TASK_KIND_RE.fullmatch(task_kind) is None:
        blocking.append("TASK_KIND_INVALID")
    revision = packet.metadata.get("INTENT_REVISION")
    if revision and (not revision.isdigit() or int(revision) < 1):
        blocking.append("INTENT_REVISION_INVALID")
    change_risk = packet.metadata.get("CHANGE_RISK")
    if change_risk and change_risk not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        blocking.append("CHANGE_RISK_INVALID")
    root = (
        Path(profile_root)
        if profile_root is not None
        else Path(__file__).resolve().parents[1]
    )
    try:
        execution_profile = load_profile(root)
    except ProfileError:
        blocking.append("EXECUTION_PROFILE_UNAVAILABLE")
    else:
        profile_blocking, profile_warnings = packet_authority(execution_profile, packet.metadata)
        blocking.extend(profile_blocking)
        compatibility.extend(profile_warnings)
    status = packet.metadata.get("STATUS")
    if status and status not in ALLOWED_STATUSES:
        blocking.append("STATUS_INVALID")
    if status == "ACTIVE":
        queue_state = (packet.metadata.get("QUEUE_STATE") or "").strip().upper()
        if queue_state in {"WAITING", "WAITING_CI", "DEPENDENCY_WAIT", "DEFERRED", "HUMAN_REQUIRED"}:
            blocking.append("ACTIVE_PACKET_NOT_RUNNABLE")
        blockers = packet.sections.get("Blockers", "").strip()
        if blockers and blockers.upper() not in {"NONE", "- NONE", "NONE."}:
            blocking.append("ACTIVE_PACKET_HAS_BLOCKER")
    if version == "3":
        for key, sentinel in TEMPLATE_META_SENTINELS.items():
            if packet.metadata.get(key) == sentinel:
                blocking.append(f"PACKET_TEMPLATE_PLACEHOLDER:{key}")
        for section, sentinel in TEMPLATE_SECTION_SENTINELS.items():
            body = packet.sections.get(section, "")
            if sentinel in body:
                blocking.append(f"PACKET_TEMPLATE_PLACEHOLDER:{section}")
    for section in REQUIRED_SECTIONS:
        if section not in packet.sections:
            blocking.append(f"MISSING_SECTION:{section}")
    for section in packet.duplicate_sections:
        if section in CANONICAL_SECTIONS:
            blocking.append(f"DUPLICATE_SECTION:{section}")
    noncanonical = sorted(set(packet.headings).difference(CANONICAL_SECTIONS))
    if noncanonical:
        reasons.append("NONCANONICAL_HISTORY_SECTIONS")
    if warn_chars is not None and packet.char_count > warn_chars:
        reasons.append("CHAR_CANARY_EXCEEDED")
    if warn_lines is not None and packet.line_count > warn_lines:
        reasons.append("LINE_CANARY_EXCEEDED")
    state = "BLOCK" if blocking else ("WARN" if reasons else "PASS")
    return {
        "status": state,
        "blocking": sorted(set(blocking)),
        "warnings": sorted(set(reasons)),
        "compatibility": sorted(set(compatibility)),
        "metrics": {
            "chars": packet.char_count,
            "lines": packet.line_count,
            "sections": len(packet.headings),
            "noncanonical_sections": len(noncanonical),
        },
        "noncanonical_sections": noncanonical,
    }


def _bounded_section(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    suffix = "\n...[SECTION_TRUNCATED: inspect authoritative Issue only if required]"
    keep = max(0, cap - len(suffix))
    return text[:keep].rstrip() + suffix, True


def project_packet(
    packet: Packet,
    *,
    section_char_cap: int = DEFAULT_SECTION_CHAR_CAP,
    projection_char_cap: int = DEFAULT_PROJECTION_CHAR_CAP,
) -> str:
    if section_char_cap < 256 or projection_char_cap < 1024:
        raise ContextError("PROJECTION_BOUND_INVALID")
    audit = analyze_packet(packet)
    if audit["status"] == "BLOCK":
        raise ContextError("PACKET_STRUCTURE_INVALID:" + ",".join(audit["blocking"]))
    content_cap = projection_char_cap - PROJECTION_TAIL_RESERVE
    if content_cap < 512:
        raise ContextError("PROJECTION_BOUND_INVALID")
    required_cap = max(256, (content_cap - 2048) // len(REQUIRED_SECTIONS))
    effective_section_cap = min(section_char_cap, required_cap)
    chunks: list[str] = []
    truncated_metadata: list[str] = []
    for key in META_ORDER:
        value = packet.metadata.get(key)
        if not value:
            continue
        bounded, clipped = _bounded_section(value, DEFAULT_META_VALUE_CAP)
        bounded = bounded.replace("\n", " ")
        if clipped:
            truncated_metadata.append(key)
        chunks.append(f"{key}={bounded}")
    chunks.append(
        "PACKET_CONTEXT_AUDIT="
        + audit["status"]
        + ";warnings="
        + (",".join(audit["warnings"]) or "NONE")
        + ";compatibility="
        + (",".join(audit["compatibility"]) or "NONE")
    )
    chunks.append(
        "PACKET_CONTEXT_METRICS="
        + json.dumps(audit["metrics"], sort_keys=True, separators=(",", ":"))
    )
    truncated: list[str] = []
    optional_omitted = False
    for name in PROJECT_SECTIONS:
        if name not in packet.sections:
            continue
        body, clipped = _bounded_section(packet.sections[name], effective_section_cap)
        if clipped:
            truncated.append(name)
        candidate = "\n\n".join(chunks + [f"## {name}\n\n{body}".rstrip()])
        if len(candidate) > content_cap:
            if name in REQUIRED_SECTIONS:
                raise ContextError(f"PROJECTION_REQUIRED_SECTION_UNFIT:{name}")
            optional_omitted = True
            break
        chunks.append(f"## {name}\n\n{body}".rstrip())
    if truncated_metadata:
        chunks.append("TRUNCATED_METADATA=" + ",".join(truncated_metadata))
    if truncated:
        chunks.append("TRUNCATED_SECTIONS=" + ",".join(truncated))
    if optional_omitted:
        chunks.append("PROJECTION_TRUNCATED=YES")
    chunks.append("PACKET_PROJECTION=PASS")
    rendered = "\n\n".join(chunks) + "\n"
    if len(rendered) > projection_char_cap:
        raise ContextError("PROJECTION_BOUND_EXCEEDED")
    return rendered


def packet_identity(packet: Packet) -> str:
    """Emit only bounded structural identity safe for pre-authority selection."""
    audit = analyze_packet(packet)
    if audit["status"] == "BLOCK":
        raise ContextError("PACKET_IDENTITY_INVALID:" + ",".join(audit["blocking"]))
    lines = [
        f"{key}={packet.metadata.get(key, MISSING_IDENTITY_VALUE)}"
        for key in IDENTITY_KEYS
    ]
    lines.append(f"PACKET_BODY_SHA256={packet.body_sha256}")
    lines.append(f"PACKET_CONTEXT_AUDIT={audit['status']}")
    lines.append("PACKET_IDENTITY=PASS")
    return "\n".join(lines) + "\n"


def load_identity_binding(path: str) -> tuple[dict[str, str], str]:
    """Load bounded structural identity emitted by packet-identity."""
    text = _read_text(path)
    if len(text) > 4096 or len(text.splitlines()) > 16:
        raise ContextError("PACKET_IDENTITY_BINDING_OVERSIZED")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        match = META_RE.fullmatch(raw)
        if not match:
            raise ContextError("PACKET_IDENTITY_BINDING_INVALID")
        key, value = match.group(1), match.group(2).strip()
        if key in values:
            raise ContextError(f"PACKET_IDENTITY_BINDING_DUPLICATE:{key}")
        values[key] = value
    if values.get("PACKET_IDENTITY") != "PASS":
        raise ContextError("PACKET_IDENTITY_BINDING_NOT_PASS")
    audit = values.get("PACKET_CONTEXT_AUDIT")
    if audit not in {"PASS", "WARN"}:
        raise ContextError("PACKET_IDENTITY_BINDING_AUDIT_INVALID")
    missing = [key for key in IDENTITY_BINDING_KEYS if key not in values]
    if missing:
        raise ContextError("PACKET_IDENTITY_BINDING_MISSING:" + ",".join(missing))
    digest = values["PACKET_BODY_SHA256"]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ContextError("PACKET_BODY_SHA256_INVALID")
    return {key: values[key] for key in IDENTITY_KEYS}, digest


def require_identity(
    packet: Packet,
    expected: dict[str, str | None],
    *,
    expected_body_sha256: str | None = None,
) -> None:
    """Fail closed when a refetched packet no longer matches selected bytes."""
    audit = analyze_packet(packet)
    if audit["status"] == "BLOCK":
        raise ContextError("PACKET_IDENTITY_INVALID:" + ",".join(audit["blocking"]))
    mismatches: list[str] = []
    for key in IDENTITY_KEYS:
        value = expected.get(key)
        if value is None:
            continue
        actual = packet.metadata.get(key)
        if value == MISSING_IDENTITY_VALUE:
            if actual is not None:
                mismatches.append(key)
        elif actual != value:
            mismatches.append(key)
    if expected_body_sha256 is not None:
        if re.fullmatch(r"[0-9a-f]{64}", expected_body_sha256) is None:
            raise ContextError("PACKET_BODY_SHA256_INVALID")
        if packet.body_sha256 != expected_body_sha256:
            mismatches.append("PACKET_BODY_SHA256")
    if mismatches:
        raise ContextError("PACKET_IDENTITY_MISMATCH:" + ",".join(mismatches))


def _bool(facts: dict[str, Any], key: str, default: bool = False) -> bool:
    value = facts.get(key, default)
    if not isinstance(value, bool):
        raise ContextError(f"FACT_INVALID:{key}")
    return value


def _validate_precompact(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContextError("PRECOMPACT_INVALID")
    result: dict[str, Any] = {}
    for key in ("context_tokens", "context_window_size", "message_count", "messages_to_compact"):
        item = value.get(key)
        if item is not None:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ContextError(f"PRECOMPACT_INVALID:{key}")
            result[key] = item
    percent = value.get("context_usage_percent")
    if percent is not None:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise ContextError("PRECOMPACT_INVALID:context_usage_percent")
        result["context_usage_percent"] = percent
    for key in ("trigger", "is_first_compaction"):
        if key in value:
            item = value[key]
            if key == "is_first_compaction" and not isinstance(item, bool):
                raise ContextError("PRECOMPACT_INVALID:is_first_compaction")
            if key == "trigger" and item not in {"auto", "manual"}:
                raise ContextError("PRECOMPACT_INVALID:trigger")
            result[key] = item
    return result


def decide_epoch(facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(facts, dict):
        raise ContextError("FACTS_INVALID")
    in_flight = _bool(facts, "in_flight")
    unreconciled = _bool(facts, "unreconciled_mutation")
    checkpoint = _bool(facts, "durable_checkpoint")
    same_atomic = _bool(facts, "same_atomic_task", True)
    boundary = any(
        _bool(facts, key)
        for key in (
            "logical_boundary", "workstream_changed", "next_action_changed",
            "profile_change_pending", "repeated_failure",
        )
    )
    precompact = _validate_precompact(facts.get("precompact"))
    if in_flight or unreconciled:
        return {"action": "CONTINUE", "reason": "MUTATION_IN_FLIGHT"}
    if boundary and not checkpoint:
        return {"action": "CHECKPOINT_REQUIRED", "reason": "DURABLE_STATE_REQUIRED"}
    if boundary and checkpoint:
        return {"action": "CLEAR", "reason": "SEMANTIC_BOUNDARY"}
    if precompact is not None:
        if same_atomic:
            return {
                "action": "SUMMARIZE",
                "reason": "NATIVE_PRECOMPACT_SAME_ATOMIC_TASK",
                "precompact": precompact,
            }
        if checkpoint:
            return {
                "action": "CLEAR",
                "reason": "NATIVE_PRECOMPACT_NEW_CONTEXT",
                "precompact": precompact,
            }
        return {
            "action": "CHECKPOINT_REQUIRED",
            "reason": "NATIVE_PRECOMPACT_NEEDS_CHECKPOINT",
            "precompact": precompact,
        }
    return {"action": "CONTINUE", "reason": "NO_BOUNDARY"}


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def sanitize_hook(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContextError("HOOK_INVALID")
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in NATIVE_EVENTS:
        raise ContextError("HOOK_EVENT_UNSUPPORTED")
    result: dict[str, Any] = {"hook_event_name": event}
    for key in ("conversation_id", "generation_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            result[key + "_hash"] = _hash_id(value)
    for key in ("client_version", "model", "model_id", "trigger", "status"):
        value = payload.get(key)
        if isinstance(value, str) and SAFE_HOOK_LABEL_RE.fullmatch(value):
            result[key] = value
    params = payload.get("model_params")
    if isinstance(params, list):
        safe_params: list[dict[str, str]] = []
        for item in params:
            if not isinstance(item, dict):
                continue
            param_id = item.get("id")
            param_value = item.get("value")
            if (
                param_id in {"effort", "reasoning", "thinking", "context"}
                and isinstance(param_value, str)
                and SAFE_HOOK_LABEL_RE.fullmatch(param_value)
            ):
                safe_params.append({"id": param_id, "value": param_value})
        if safe_params:
            result["model_params"] = safe_params
    for key in (
        "context_tokens", "context_window_size", "message_count",
        "messages_to_compact", "loop_count", "duration_ms",
    ):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    percent = payload.get("context_usage_percent")
    if isinstance(percent, (int, float)) and not isinstance(percent, bool) and 0 <= percent <= 100:
        result["context_usage_percent"] = percent
    first = payload.get("is_first_compaction")
    if isinstance(first, bool):
        result["is_first_compaction"] = first
    return result


def _load_json(path: str) -> Any:
    text = _read_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContextError("JSON_INVALID") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    identity = sub.add_parser("packet-identity")
    identity.add_argument("--body-file", required=True)

    project = sub.add_parser("packet-project")
    project.add_argument("--body-file", required=True)
    project.add_argument("--section-char-cap", type=int, default=DEFAULT_SECTION_CHAR_CAP)
    project.add_argument("--projection-char-cap", type=int, default=DEFAULT_PROJECTION_CHAR_CAP)
    project.add_argument("--expect-identity-file")
    for key in IDENTITY_KEYS:
        project.add_argument("--expect-" + key.lower().replace("_", "-"))
    project.add_argument("--expect-body-sha256")

    lint = sub.add_parser("packet-lint")
    lint.add_argument("--body-file", required=True)
    lint.add_argument("--root")
    lint.add_argument("--expect-target-repo")
    lint.add_argument("--warn-chars", type=int)
    lint.add_argument("--warn-lines", type=int)

    decide = sub.add_parser("epoch-decide")
    decide.add_argument("--facts", required=True)

    hook = sub.add_parser("sanitize-hook")
    hook.add_argument("--event", required=True)

    args = parser.parse_args()
    try:
        if args.command == "packet-identity":
            sys.stdout.write(packet_identity(parse_packet(_read_text(args.body_file))))
        elif args.command == "packet-project":
            packet = parse_packet(_read_text(args.body_file))
            direct_expected = {
                key: getattr(args, "expect_" + key.lower())
                for key in IDENTITY_KEYS
            }
            if args.expect_identity_file and (
                any(value is not None for value in direct_expected.values())
                or args.expect_body_sha256 is not None
            ):
                raise ContextError("PACKET_IDENTITY_BINDING_AMBIGUOUS")
            if args.expect_identity_file:
                expected, expected_digest = load_identity_binding(args.expect_identity_file)
            else:
                expected, expected_digest = direct_expected, args.expect_body_sha256
            require_identity(
                packet,
                expected,
                expected_body_sha256=expected_digest,
            )
            sys.stdout.write(project_packet(
                packet,
                section_char_cap=args.section_char_cap,
                projection_char_cap=args.projection_char_cap,
            ))
        elif args.command == "packet-lint":
            result = analyze_packet(
                parse_packet(_read_text(args.body_file)),
                profile_root=args.root,
                expected_target_repo=args.expect_target_repo,
                warn_chars=args.warn_chars,
                warn_lines=args.warn_lines,
            )
            print(json.dumps(result, sort_keys=True))
            return 2 if result["status"] == "BLOCK" else 0
        elif args.command == "epoch-decide":
            print(json.dumps(decide_epoch(_load_json(args.facts)), sort_keys=True))
        else:
            print(json.dumps(sanitize_hook(_load_json(args.event)), sort_keys=True))
    except ContextError as exc:
        print(f"CONTEXT_EPOCH=BLOCK reason={exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
