#!/usr/bin/env python3
"""Native Cursor hook receipts and canonical telemetry for one benchmark lane.

Qualified receipts finalize only through ``efficiency_telemetry``. This module
does not start a benchmark worker. Native hook JSON is the only event source.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_execution
import benchmark_fixture
import efficiency_telemetry

ROOT = TOOLS.parent
PLUGIN_ROOT = ROOT / "benchmarks" / "persistent-instrumentation"
PLUGIN_VERSION = "1.0.0"
PLUGIN_NAME = "benchmark-lane-instrumentation"
PLUGIN_FILES = (
    ".cursor-plugin/plugin.json",
    "hooks/hooks.json",
    "hooks/record.py",
)
NATIVE_EVENTS = frozenset(
    {
        "sessionStart",
        "sessionEnd",
        "beforeSubmitPrompt",
        "preToolUse",
        "beforeShellExecution",
        "postToolUse",
        "postToolUseFailure",
        "preCompact",
        "stop",
    }
)
BLOCKING_HOOKS = frozenset({"beforeSubmitPrompt", "preToolUse", "beforeShellExecution"})
NORMAL_SESSION_CLOSE = frozenset({"completed", "window_close", "user_close"})
# Native reasoning level is model_params id "effort". "reasoning" is the legacy alias.
# "thinking" is a separate boolean mode and is not a reasoning level.
REASONING_PARAM_IDS = frozenset({"effort", "reasoning"})
TOOL_CATEGORIES = {
    "Read": "read",
    "Grep": "read",
    "Glob": "read",
    "WebSearch": "read",
    "WebFetch": "read",
    "Write": "write",
    "Edit": "write",
    "Shell": "shell",
    "Bash": "shell",
}
BODY_KEYS = (
    "schema_version",
    "kind",
    "run_id",
    "case_id",
    "lane",
    "repository",
    "task_source_head",
    "system_head",
    "profile",
    "expected_effective_sandbox",
    "plugin_version",
    "plugin_digest",
    "adapter_digest",
    "cursor_version",
)
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
RUN_RE = re.compile(r"^[a-f0-9]{32}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{6,40}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT_BYTES = 16384
MAX_EVENTS = 256


class CaptureError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def adapter_digest(path: Path | None = None) -> str:
    source = Path(__file__).resolve() if path is None else path
    if not source.is_file():
        raise CaptureError("ADAPTER_DIGEST_MISMATCH")
    return _sha256(source.read_bytes())


def plugin_digest(plugin_root: Path) -> str:
    hasher = hashlib.sha256()
    for relative in PLUGIN_FILES:
        path = plugin_root / relative
        if not path.is_file():
            raise CaptureError("PLUGIN_INCOMPLETE")
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _outside(path: Path, task_root: Path) -> None:
    resolved = path.resolve()
    root = task_root.resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise CaptureError("TASK_TREE_MUTATION")


def _content_free(value: Any, *, field: str | None = None) -> None:
    if isinstance(value, dict):
        if efficiency_telemetry.PROHIBITED_KEYS.intersection(value):
            raise CaptureError("PROHIBITED_CONTENT")
        for key, item in value.items():
            _content_free(item, field=key if isinstance(key, str) else None)
        return
    if isinstance(value, list):
        for item in value:
            _content_free(item, field=field)
        return
    if not isinstance(value, str):
        return
    if field == "repository":
        if REPO_RE.fullmatch(value) is None:
            raise CaptureError("PROHIBITED_CONTENT")
        return
    if "/" in value or value.startswith("~") or "\\" in value or ".." in value or "@" in value:
        raise CaptureError("PROHIBITED_CONTENT")
    if efficiency_telemetry.SECRET_VALUE_RE.search(value):
        raise CaptureError("PROHIBITED_CONTENT")


def _identity(value: Any, code: str) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise CaptureError(code)
    return value


def _expected_system_head(lane: str) -> str:
    if lane == "CONTROL":
        return benchmark_fixture.CONTROL_HEAD
    if lane == "CANDIDATE":
        return benchmark_fixture.CANDIDATE_HEAD
    raise CaptureError("LANE_INVALID")


def _profile(profile: dict[str, Any]) -> dict[str, str]:
    try:
        normalized = efficiency_telemetry.normalize_profile(profile)
    except efficiency_telemetry.TelemetryError as exc:
        raise CaptureError(exc.code) from exc
    if any(benchmark_execution.NAME_RE.fullmatch(value) is None for value in normalized.values()):
        raise CaptureError("PROFILE_INCOMPLETE")
    return {field: normalized[field] for field in efficiency_telemetry.PROFILE_FIELDS}


def _validate_plugin_manifest(plugin_root: Path) -> None:
    manifest_path = plugin_root / ".cursor-plugin" / "plugin.json"
    hooks_path = plugin_root / "hooks" / "hooks.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("PLUGIN_INCOMPLETE") from exc
    if manifest.get("name") != PLUGIN_NAME or manifest.get("version") != PLUGIN_VERSION:
        raise CaptureError("PLUGIN_MISMATCH")
    if manifest.get("hooks") != "./hooks/hooks.json":
        raise CaptureError("PLUGIN_MISMATCH")
    commands = hooks.get("hooks")
    if hooks.get("version") != 1 or not isinstance(commands, dict):
        raise CaptureError("PLUGIN_MISMATCH")
    if set(commands) != NATIVE_EVENTS:
        raise CaptureError("PLUGIN_MISMATCH")
    expected = "python3 ${CURSOR_PLUGIN_ROOT}/hooks/record.py"
    for event, entries in commands.items():
        if not isinstance(entries, list) or len(entries) != 1:
            raise CaptureError("PLUGIN_MISMATCH")
        entry = entries[0] if isinstance(entries[0], dict) else None
        if entry is None or set(entry) - {"command", "failClosed"}:
            raise CaptureError("PLUGIN_MISMATCH")
        command = entry.get("command")
        if command != expected or event not in NATIVE_EVENTS:
            raise CaptureError("PLUGIN_MISMATCH")
        if ".cursor/hooks.json" in command or "~/.cursor" in command:
            raise CaptureError("PLUGIN_MISMATCH")
        if event in BLOCKING_HOOKS:
            if entry.get("failClosed") is not True:
                raise CaptureError("PLUGIN_MISMATCH")
        elif entry.get("failClosed"):
            raise CaptureError("PLUGIN_MISMATCH")


def stage_plugin(snapshot_dir: Path, *, task_root: Path, source: Path | None = None) -> str:
    """Copy the hook-only plugin outside the historical task tree."""
    source = PLUGIN_ROOT if source is None else source
    _outside(snapshot_dir, task_root)
    if snapshot_dir.exists():
        raise CaptureError("PLUGIN_SNAPSHOT_EXISTS")
    _validate_plugin_manifest(source)
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for relative in PLUGIN_FILES:
        destination = snapshot_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    _validate_plugin_manifest(snapshot_dir)
    return plugin_digest(snapshot_dir)


def _descriptor_body(
    *,
    run_id: str,
    case_id: str,
    lane: str,
    profile: dict[str, str],
    expected_effective_sandbox: str,
    plugin_digest_value: str,
    adapter_digest_value: str,
    cursor_version: str,
) -> dict[str, Any]:
    if RUN_RE.fullmatch(run_id) is None:
        raise CaptureError("RUN_ID_INVALID")
    if VERSION_RE.fullmatch(cursor_version) is None:
        raise CaptureError("VERSION_UNVERIFIED")
    if HEX_RE.fullmatch(plugin_digest_value) is None:
        raise CaptureError("PLUGIN_DIGEST_MISMATCH")
    if adapter_digest_value != adapter_digest():
        raise CaptureError("ADAPTER_DIGEST_MISMATCH")
    if expected_effective_sandbox not in {"enabled", "disabled"}:
        raise CaptureError("SANDBOX_UNVERIFIED")
    identity = benchmark_execution.FROZEN_CASE_IDENTITIES.get(case_id)
    if identity is None:
        raise CaptureError("CASE_NOT_IN_PILOT")
    system_head = _expected_system_head(lane)
    return {
        "schema_version": 1,
        "kind": "benchmark-lane-descriptor",
        "run_id": run_id,
        "case_id": case_id,
        "lane": lane,
        "repository": identity["repository"],
        "task_source_head": identity["source_commit"],
        "system_head": system_head,
        "profile": profile,
        "expected_effective_sandbox": expected_effective_sandbox,
        "plugin_version": PLUGIN_VERSION,
        "plugin_digest": plugin_digest_value,
        "adapter_digest": adapter_digest_value,
        "cursor_version": cursor_version,
    }


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    document = {key: body[key] for key in BODY_KEYS}
    document["descriptor_digest"] = _sha256(_canonical(document))
    _content_free(document)
    return document


def _rollback(created: list[Path]) -> None:
    for path in reversed(created):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def descriptor_path(state_dir: Path, run_id: str, lane: str) -> Path:
    return state_dir / f"{run_id}-{lane}.descriptor.json"


def receipt_path(state_dir: Path, run_id: str, lane: str) -> Path:
    return state_dir / f"{run_id}-{lane}.receipt.json"


def ephemeral_key_path(state_dir: Path, run_id: str, lane: str) -> Path:
    return state_dir / f"{run_id}-{lane}.hmac-key"


def fingerprint_path(state_dir: Path, run_id: str, lane: str) -> Path:
    return state_dir / f"{run_id}-{lane}.fingerprints.json"


def prepare_lane(
    *,
    state_dir: Path,
    task_root: Path,
    snapshot_dir: Path,
    run_id: str,
    case_id: str,
    lane: str,
    profile: dict[str, Any],
    cursor_version: str,
    expected_effective_sandbox: str,
) -> dict[str, Any]:
    """Write one immutable descriptor and stage the plugin snapshot."""
    _outside(state_dir, task_root)
    _outside(snapshot_dir, task_root)
    normalized = _profile(profile)
    destination = descriptor_path(state_dir, run_id, lane)
    key_path = ephemeral_key_path(state_dir, run_id, lane)
    fingers = fingerprint_path(state_dir, run_id, lane)
    for path in (destination, key_path, fingers):
        _outside(path, task_root)
    if destination.exists() or key_path.exists() or fingers.exists():
        raise CaptureError("DESCRIPTOR_EXISTS")
    if snapshot_dir.exists():
        raise CaptureError("PLUGIN_SNAPSHOT_EXISTS")
    created: list[Path] = []
    try:
        digest = stage_plugin(snapshot_dir, task_root=task_root)
        created.append(snapshot_dir)
        if digest != plugin_digest(PLUGIN_ROOT):
            raise CaptureError("PLUGIN_DIGEST_MISMATCH")
        document = _seal(
            _descriptor_body(
                run_id=run_id,
                case_id=case_id,
                lane=lane,
                profile=normalized,
                expected_effective_sandbox=expected_effective_sandbox,
                plugin_digest_value=digest,
                adapter_digest_value=adapter_digest(),
                cursor_version=cursor_version,
            )
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("xb") as handle:
                handle.write(secrets.token_bytes(32))
        except FileExistsError as exc:
            raise CaptureError("DESCRIPTOR_EXISTS") from exc
        key_path.chmod(0o600)
        created.append(key_path)
        encoded = _canonical(document) + b"\n"
        try:
            with destination.open("xb") as handle:
                handle.write(encoded)
        except FileExistsError as exc:
            raise CaptureError("DESCRIPTOR_EXISTS") from exc
        created.append(destination)
        destination.chmod(0o444)
    except Exception:
        _rollback(created)
        raise
    return {
        "descriptor_path": str(destination),
        "plugin_dir": str(snapshot_dir.resolve()),
        "plugin_digest": digest,
        "argv": ["--plugin-dir", str(snapshot_dir.resolve())],
        "env": {
            "ES_BENCHMARK_LANE_DESCRIPTOR": str(destination),
            "ES_BENCHMARK_TELEMETRY_MODULE": str(Path(__file__).resolve()),
            "ES_BENCHMARK_PLUGIN_ROOT": str(snapshot_dir.resolve()),
        },
        "execute_worker": False,
    }


def _load_descriptor(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("DESCRIPTOR_MUTATED") from exc
    if not isinstance(document, dict) or set(document) != set(BODY_KEYS) | {"descriptor_digest"}:
        raise CaptureError("DESCRIPTOR_MUTATED")
    body = {key: document[key] for key in BODY_KEYS}
    if document["descriptor_digest"] != _sha256(_canonical(body)):
        raise CaptureError("DESCRIPTOR_MUTATED")
    expected = _seal(
        _descriptor_body(
            run_id=body["run_id"],
            case_id=body["case_id"],
            lane=body["lane"],
            profile=_profile(body["profile"]),
            expected_effective_sandbox=body["expected_effective_sandbox"],
            plugin_digest_value=body["plugin_digest"],
            adapter_digest_value=body["adapter_digest"],
            cursor_version=body["cursor_version"],
        )
    )
    if document != expected:
        raise CaptureError("DESCRIPTOR_MUTATED")
    if path.name != f"{document['run_id']}-{document['lane']}.descriptor.json":
        raise CaptureError("DESCRIPTOR_MUTATED")
    _content_free(document)
    return document


def _blank_receipt(descriptor: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "benchmark-hook-receipt",
        "descriptor_digest": descriptor["descriptor_digest"],
        "plugin_digest": descriptor["plugin_digest"],
        "handshake": None,
        "lifecycle": "OPEN",
        "block": None,
        "events": [],
    }


def _load_receipt(path: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return _blank_receipt(descriptor)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("RECEIPT_MUTATED") from exc
    if not isinstance(document, dict) or document.get("kind") != "benchmark-hook-receipt":
        raise CaptureError("RECEIPT_MUTATED")
    if document.get("descriptor_digest") != descriptor["descriptor_digest"]:
        raise CaptureError("DESCRIPTOR_MISMATCH")
    _content_free(document)
    return document


def _write_receipt(path: Path, receipt: dict[str, Any], task_root: Path | None) -> None:
    _content_free(receipt)
    encoded = _canonical(receipt) + b"\n"
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise CaptureError("RECEIPT_UNBOUNDED")
    if task_root is not None:
        _outside(path, task_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _conversation(payload: dict[str, Any]) -> str:
    conversation = payload.get("conversation_id")
    session = payload.get("session_id")
    if conversation is None and session is None:
        raise CaptureError("HANDSHAKE_INCOMPLETE")
    if conversation is not None:
        conversation = _identity(conversation, "IDENTITY_INVALID")
    if session is not None:
        session = _identity(session, "IDENTITY_INVALID")
    if conversation is not None and session is not None and conversation != session:
        raise CaptureError("SESSION_MISMATCH")
    return conversation or session


def _optional_id(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    return _identity(payload[key], "IDENTITY_INVALID")


def _category(payload: dict[str, Any]) -> str | None:
    if "tool_name" not in payload:
        return None
    name = payload.get("tool_name")
    if not isinstance(name, str):
        raise CaptureError("IDENTITY_INVALID")
    return TOOL_CATEGORIES.get(name, "other")


def _load_key(path: Path) -> bytes:
    try:
        key = path.read_bytes()
    except OSError as exc:
        raise CaptureError("FINGERPRINT_KEY_MISSING") from exc
    if len(key) != 32:
        raise CaptureError("FINGERPRINT_KEY_INVALID")
    return key


def _load_fingerprints(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError("RECEIPT_MUTATED") from exc
    if not isinstance(document, dict) or document.get("kind") != "benchmark-hook-fingerprints":
        raise CaptureError("RECEIPT_MUTATED")
    values = document.get("fingerprints")
    if not isinstance(values, dict):
        raise CaptureError("RECEIPT_MUTATED")
    _content_free(document)
    return {str(key): value for key, value in values.items()}


def _write_fingerprints(path: Path, fingerprints: dict[str, str]) -> None:
    document = {"schema_version": 1, "kind": "benchmark-hook-fingerprints", "fingerprints": fingerprints}
    _content_free(document)
    path.write_bytes(_canonical(document) + b"\n")


def _delete_ephemeral(state_dir: Path, run_id: str, lane: str) -> None:
    for path in (ephemeral_key_path(state_dir, run_id, lane), fingerprint_path(state_dir, run_id, lane)):
        path.unlink(missing_ok=True)


def _fingerprint(category: str | None, payload: dict[str, Any], key: bytes) -> str | None:
    if "tool_input" not in payload or category is None:
        return None
    try:
        encoded = json.dumps(payload["tool_input"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CaptureError("PROHIBITED_CONTENT") from exc
    return hmac.new(key, category.encode("utf-8") + b"\0" + encoded, hashlib.sha256).hexdigest()


def _synthetic(payload: dict[str, Any]) -> None:
    name = payload.get("hook_event_name")
    if not isinstance(name, str) or name not in NATIVE_EVENTS:
        raise CaptureError("SYNTHETIC_MARKER" if _marker(payload) else "UNMAPPED_EVENT")
    if _marker(payload):
        raise CaptureError("SYNTHETIC_MARKER")


def _marker(payload: dict[str, Any]) -> bool:
    encoded = json.dumps(payload, sort_keys=True)
    return "ES-EVENT" in encoded or "ES-PTY" in encoded or "es-pty-v1" in encoded


def _append_event(receipt: dict[str, Any], event: dict[str, Any]) -> None:
    events = receipt["events"]
    # A generation identifies a prompt. It does not identify a compaction call.
    if event.get("hook_event_name") == "preCompact":
        if len(events) >= MAX_EVENTS:
            raise CaptureError("RECEIPT_UNBOUNDED")
        events.append(event)
        return
    identity = event.get("tool_use_id") or event.get("generation_id")
    if identity is not None:
        previous = [
            item
            for item in events
            if item.get("hook_event_name") == event["hook_event_name"]
            and (item.get("tool_use_id") or item.get("generation_id")) == identity
        ]
        if previous and previous[-1] != event:
            raise CaptureError("DUPLICATE_EVENT")
        if previous:
            return
    if len(events) >= MAX_EVENTS:
        raise CaptureError("RECEIPT_UNBOUNDED")
    events.append(event)


def assess(receipt: dict[str, Any]) -> str | None:
    """Return the fail-closed reason, or None when the receipt lifecycle is complete."""
    if receipt.get("block"):
        return receipt["block"]
    if receipt.get("handshake") is None:
        return "HANDSHAKE_MISSING"
    if receipt.get("lifecycle") != "COMPLETE":
        return "INCOMPLETE_LIFECYCLE"
    return None


def gate_launch(descriptor_path: Path) -> dict[str, Any]:
    """Admit a later benchmark launch only after a native activation handshake."""
    try:
        descriptor = _load_descriptor(descriptor_path)
        receipt = _load_receipt(receipt_path(descriptor_path.parent, descriptor["run_id"], descriptor["lane"]), descriptor)
        reason = assess(receipt)
    except CaptureError as exc:
        reason = exc.code
    return {
        "admission": "BLOCK" if reason else "READY",
        "reason": reason or "HANDSHAKE_BOUND",
        "execute_worker": False,
    }


def hook_response(payload: dict[str, Any] | None, *, blocked: bool) -> dict[str, Any]:
    name = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if name in {"preToolUse", "beforeShellExecution"}:
        return {"permission": "deny" if blocked else "allow"}
    if name == "beforeSubmitPrompt":
        return {"continue": False if blocked else True}
    if blocked:
        return {"permission": "deny", "continue": False}
    return {}


def _result(payload: Any, *, status: str, reason: str, blocked: bool) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "blocked": blocked,
        "hook_response": hook_response(payload if isinstance(payload, dict) else None, blocked=blocked),
    }


def _duration_seconds(started: str, finished: str) -> int:
    try:
        start = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(finished, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CaptureError("DURATION_AMBIGUOUS") from exc
    if end < start:
        raise CaptureError("DURATION_AMBIGUOUS")
    seconds = int((end - start).total_seconds())
    if seconds > 86400:
        raise CaptureError("DURATION_AMBIGUOUS")
    return seconds


def _note_duration_ms(payload: dict[str, Any], receipt: dict[str, Any]) -> None:
    if "duration_ms" not in payload:
        return
    value = payload["duration_ms"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value % 1000:
        raise CaptureError("DURATION_AMBIGUOUS")
    previous = receipt.get("duration_ms")
    if previous is not None and previous != value:
        raise CaptureError("DURATION_AMBIGUOUS")
    receipt["duration_ms"] = value


def _observe_usage(payload: dict[str, Any], receipt: dict[str, Any]) -> None:
    if "usage" not in payload or payload["usage"] is None:
        return
    try:
        parsed = efficiency_telemetry.usage_from_exposed(payload["usage"])
    except efficiency_telemetry.TelemetryError as exc:
        raise CaptureError(exc.code) from exc
    previous = receipt.get("usage")
    if previous is not None and previous != parsed:
        raise CaptureError("USAGE_AMBIGUOUS")
    receipt["usage"] = parsed


def _derive_counts(receipt: dict[str, Any], fingerprints: dict[str, str]) -> dict[str, int]:
    """Derive counters while keyed fingerprints still exist. Missing correlation blocks."""
    events = receipt.get("events")
    if not isinstance(events, list):
        raise CaptureError("MISSING_TELEMETRY")
    if benchmark_execution.BENCHMARK_NETWORK != "NONE":
        raise CaptureError("MISSING_TELEMETRY")
    pres: dict[str, str] = {}
    pre_order: list[str] = []
    posts: dict[str, str] = {}
    compactions = 0
    prompts: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            raise CaptureError("MISSING_TELEMETRY")
        name = event.get("hook_event_name")
        if name == "preCompact":
            if not isinstance(event.get("generation_id"), str):
                raise CaptureError("MISSING_TELEMETRY")
            compactions += 1
            continue
        if name == "beforeSubmitPrompt":
            generation = event.get("generation_id")
            if not isinstance(generation, str):
                raise CaptureError("MISSING_TELEMETRY")
            if generation not in prompts:
                prompts.append(generation)
            continue
        if name not in {"preToolUse", "postToolUse", "postToolUseFailure"}:
            continue
        tool = event.get("tool_use_id")
        category = event.get("tool_category")
        if not isinstance(tool, str) or not isinstance(category, str):
            raise CaptureError("MISSING_TELEMETRY")
        if name == "preToolUse":
            if tool in pres and pres[tool] != category:
                raise CaptureError("DUPLICATE_EVENT")
            if tool not in pres:
                pres[tool] = category
                pre_order.append(tool)
            continue
        if tool not in pres or pres[tool] != category:
            raise CaptureError("MISSING_TELEMETRY")
        previous = posts.get(tool)
        if previous is not None and previous != name:
            raise CaptureError("DUPLICATE_EVENT")
        if previous is None:
            posts[tool] = name
    if set(posts) != set(pres):
        raise CaptureError("MISSING_TELEMETRY")
    failed_fingerprints: set[str] = set()
    read_counts: dict[str, int] = {}
    retries = 0
    for tool in pre_order:
        fingerprint = fingerprints.get(tool)
        if not isinstance(fingerprint, str) or HEX_RE.fullmatch(fingerprint) is None:
            raise CaptureError("MISSING_TELEMETRY")
        if pres[tool] == "read":
            read_counts[fingerprint] = read_counts.get(fingerprint, 0) + 1
        if fingerprint in failed_fingerprints:
            retries += 1
        if posts[tool] == "postToolUseFailure":
            failed_fingerprints.add(fingerprint)
    # PR/CI/review rework is zero because the benchmark contract has no GitHub
    # and no network. That is not a substitute for a missing tool correlation.
    return {
        "tool_turns": len(pre_order),
        "retries": retries,
        "rereads": sum(count - 1 for count in read_counts.values()),
        "compactions": compactions,
        "pr_rework": 0,
        "ci_rework": 0,
        "review_rework": 0,
        "human_interventions": max(len(prompts) - 1, 0),
    }


def _record_terminal_counts(receipt: dict[str, Any], fingerprints: dict[str, str]) -> None:
    started = receipt.get("started_at")
    if not isinstance(started, str):
        raise CaptureError("MISSING_TELEMETRY")
    finished = _utc_now()
    seconds = _duration_seconds(started, finished)
    noted = receipt.get("duration_ms")
    if noted is not None and noted // 1000 != seconds:
        raise CaptureError("DURATION_AMBIGUOUS")
    receipt.pop("duration_ms", None)
    receipt["finished_at"] = finished
    receipt["duration_seconds"] = seconds
    receipt["counts"] = _derive_counts(receipt, fingerprints)


def ingest_hook(
    payload: Any,
    *,
    descriptor_path: Path,
    plugin_root: Path,
) -> dict[str, Any]:
    """Record one native hook JSON object, or block the lane."""
    try:
        descriptor = _load_descriptor(descriptor_path)
        if plugin_digest(plugin_root) != descriptor["plugin_digest"]:
            raise CaptureError("PLUGIN_DIGEST_MISMATCH")
        if descriptor["adapter_digest"] != adapter_digest():
            raise CaptureError("ADAPTER_DIGEST_MISMATCH")
        state_dir = descriptor_path.parent
        receipt_file = receipt_path(state_dir, descriptor["run_id"], descriptor["lane"])
        receipt = _load_receipt(receipt_file, descriptor)
        if receipt.get("block"):
            return _result(payload, status="BLOCK", reason=receipt["block"], blocked=True)
        if not isinstance(payload, dict):
            raise CaptureError("MALFORMED_HOOK")
        _synthetic(payload)
        fingerprints = _load_fingerprints(fingerprint_path(state_dir, descriptor["run_id"], descriptor["lane"]))
        _apply(descriptor, receipt, payload, fingerprints, state_dir)
        if receipt["lifecycle"] == "COMPLETE" and "counts" not in receipt:
            _record_terminal_counts(receipt, fingerprints)
        _write_receipt(receipt_file, receipt, None)
        if receipt["lifecycle"] == "COMPLETE":
            _delete_ephemeral(state_dir, descriptor["run_id"], descriptor["lane"])
        elif fingerprints:
            _write_fingerprints(fingerprint_path(state_dir, descriptor["run_id"], descriptor["lane"]), fingerprints)
    except CaptureError as exc:
        _block(descriptor_path, exc.code)
        return _result(payload, status="BLOCK", reason=exc.code, blocked=True)
    blocked = receipt.get("handshake") is None or receipt.get("block") is not None
    reason = assess(receipt)
    return _result(
        payload,
        status="BLOCK" if reason else "READY",
        reason=reason or "HANDSHAKE_BOUND",
        blocked=blocked,
    )


def _block(descriptor_path: Path, code: str) -> None:
    try:
        descriptor = _load_descriptor(descriptor_path)
    except CaptureError:
        return
    path = receipt_path(descriptor_path.parent, descriptor["run_id"], descriptor["lane"])
    try:
        receipt = _load_receipt(path, descriptor)
    except CaptureError:
        return
    if receipt.get("block") is None:
        receipt["block"] = code
        if code != "INCOMPLETE_LIFECYCLE":
            receipt["lifecycle"] = "BLOCKED"
    try:
        _write_receipt(path, receipt, None)
    except CaptureError:
        return
    _delete_ephemeral(descriptor_path.parent, descriptor["run_id"], descriptor["lane"])


def _observed_reasoning(payload: dict[str, Any]) -> str:
    params = payload.get("model_params")
    if not isinstance(params, list):
        raise CaptureError("MISSING_PROFILE_EVIDENCE")
    found: str | None = None
    for item in params:
        if not isinstance(item, dict) or item.get("id") not in REASONING_PARAM_IDS:
            continue
        value = item.get("value")
        if not isinstance(value, str) or benchmark_execution.NAME_RE.fullmatch(value) is None:
            raise CaptureError("PROFILE_MISMATCH")
        if found is not None and found != value:
            raise CaptureError("PROFILE_AMBIGUOUS")
        found = value
    if found is None:
        raise CaptureError("MISSING_PROFILE_EVIDENCE")
    return found


def _observe_sandbox(payload: dict[str, Any], descriptor: dict[str, Any], receipt: dict[str, Any]) -> None:
    sandbox = payload.get("sandbox")
    if not isinstance(sandbox, bool):
        raise CaptureError("MISSING_SANDBOX")
    observed = "enabled" if sandbox else "disabled"
    if observed != descriptor["expected_effective_sandbox"]:
        raise CaptureError("SANDBOX_MISMATCH")
    if receipt.get("effective_sandbox") not in (None, observed):
        raise CaptureError("SANDBOX_MISMATCH")
    receipt["effective_sandbox"] = observed


def _require_sandbox(descriptor: dict[str, Any], receipt: dict[str, Any]) -> None:
    if receipt.get("effective_sandbox") != descriptor["expected_effective_sandbox"]:
        raise CaptureError("MISSING_SANDBOX")


def _finish_stop(payload: dict[str, Any], descriptor: dict[str, Any], receipt: dict[str, Any]) -> None:
    status = _terminal_status("stop", payload)
    if receipt["lifecycle"] == "COMPLETE":
        if status == "completed":
            return
        raise CaptureError("INCOMPLETE_LIFECYCLE")
    if status != "completed":
        raise CaptureError("INCOMPLETE_LIFECYCLE")
    _require_sandbox(descriptor, receipt)
    receipt["lifecycle"] = "COMPLETE"


def _finish_session_end(payload: dict[str, Any], descriptor: dict[str, Any], receipt: dict[str, Any]) -> None:
    reason = _terminal_status("sessionEnd", payload)
    if receipt["lifecycle"] == "COMPLETE":
        if reason in NORMAL_SESSION_CLOSE or reason in {"error", "aborted"}:
            return
        raise CaptureError("INCOMPLETE_LIFECYCLE")
    if reason != "completed":
        raise CaptureError("INCOMPLETE_LIFECYCLE")
    _require_sandbox(descriptor, receipt)
    receipt["lifecycle"] = "COMPLETE"


def _terminal_status(event_name: str, payload: dict[str, Any]) -> str:
    if event_name == "stop":
        status = payload.get("status")
        if not isinstance(status, str):
            raise CaptureError("INCOMPLETE_LIFECYCLE")
        return status
    reason = payload.get("reason")
    if not isinstance(reason, str):
        raise CaptureError("INCOMPLETE_LIFECYCLE")
    return reason


def _apply(
    descriptor: dict[str, Any],
    receipt: dict[str, Any],
    payload: dict[str, Any],
    fingerprints: dict[str, str],
    state_dir: Path,
) -> None:
    event_name = payload["hook_event_name"]
    version = payload.get("cursor_version")
    if not isinstance(version, str) or version != descriptor["cursor_version"]:
        raise CaptureError("VERSION_MISMATCH")
    conversation = _conversation(payload)
    generation = _optional_id(payload, "generation_id")
    tool_use = _optional_id(payload, "tool_use_id")
    handshake = receipt["handshake"]
    if handshake is None:
        if event_name != "sessionStart":
            raise CaptureError("HANDSHAKE_MISSING")
        model = payload.get("model")
        if not isinstance(model, str) or model != descriptor["profile"]["model"]:
            raise CaptureError("PROFILE_MISMATCH")
        reasoning = _observed_reasoning(payload)
        if reasoning != descriptor["profile"]["reasoning"]:
            raise CaptureError("PROFILE_MISMATCH")
        receipt["handshake"] = {
            "cursor_version": version,
            "conversation_id": conversation,
            "session_id": conversation,
            "reasoning": reasoning,
        }
        receipt["started_at"] = _utc_now()
    elif handshake["conversation_id"] != conversation or handshake["cursor_version"] != version:
        raise CaptureError("SESSION_MISMATCH" if handshake["conversation_id"] != conversation else "VERSION_MISMATCH")
    if event_name == "beforeShellExecution":
        _observe_sandbox(payload, descriptor, receipt)
        category = "shell"
    else:
        category = _category(payload)
    _observe_usage(payload, receipt)
    _note_duration_ms(payload, receipt)
    if "tool_input" in payload and category is not None and tool_use is not None:
        key = _load_key(ephemeral_key_path(state_dir, descriptor["run_id"], descriptor["lane"]))
        fingerprint = _fingerprint(category, payload, key)
        if fingerprint is None:
            raise CaptureError("FINGERPRINT_KEY_MISSING")
        current = fingerprints.get(tool_use)
        if current is not None and current != fingerprint:
            raise CaptureError("DUPLICATE_EVENT")
        fingerprints[tool_use] = fingerprint
    if event_name == "preToolUse" and (tool_use is None or category is None or tool_use not in fingerprints):
        raise CaptureError("MISSING_TELEMETRY")
    if event_name in {"postToolUse", "postToolUseFailure"}:
        if tool_use is None or category is None:
            raise CaptureError("MISSING_TELEMETRY")
        prior_pre = [
            item
            for item in receipt["events"]
            if item.get("hook_event_name") == "preToolUse" and item.get("tool_use_id") == tool_use
        ]
        if not prior_pre or prior_pre[-1].get("tool_category") != category:
            raise CaptureError("MISSING_TELEMETRY")
        mixed = [
            item
            for item in receipt["events"]
            if item.get("tool_use_id") == tool_use
            and item.get("hook_event_name") in {"postToolUse", "postToolUseFailure"}
            and item.get("hook_event_name") != event_name
        ]
        if mixed:
            raise CaptureError("DUPLICATE_EVENT")
    if event_name in {"beforeSubmitPrompt", "preCompact"} and generation is None:
        raise CaptureError("MISSING_TELEMETRY")
    event = {
        "hook_event_name": event_name,
        "conversation_id": conversation,
        "generation_id": generation,
        "tool_use_id": tool_use,
        "tool_category": category,
    }
    _append_event(receipt, event)
    if event_name == "stop":
        _finish_stop(payload, descriptor, receipt)
    elif event_name == "sessionEnd":
        _finish_session_end(payload, descriptor, receipt)


def finalize_lane(descriptor_path: Path, *, root: Path) -> dict[str, Any]:
    """Write one canonical efficiency-telemetry record, or return a fail-closed block."""
    try:
        descriptor = _load_descriptor(descriptor_path)
        receipt = _load_receipt(
            receipt_path(descriptor_path.parent, descriptor["run_id"], descriptor["lane"]),
            descriptor,
        )
        reason = assess(receipt)
        if reason:
            return {"status": "BLOCK", "reason": reason, "execute_worker": False}
        counts = receipt.get("counts")
        if not isinstance(counts, dict) or not isinstance(receipt.get("duration_seconds"), int):
            return {"status": "BLOCK", "reason": "MISSING_TELEMETRY", "execute_worker": False}
        if not isinstance(receipt.get("started_at"), str) or not isinstance(receipt.get("finished_at"), str):
            return {"status": "BLOCK", "reason": "MISSING_TELEMETRY", "execute_worker": False}
        head = efficiency_telemetry.git_head(root)
        if head != descriptor["system_head"]:
            return {"status": "BLOCK", "reason": "EXACT_HEAD_UNVERIFIED", "execute_worker": False}
        usage = receipt.get("usage")
        record = efficiency_telemetry.build_record(
            repo=descriptor["repository"],
            workstream=benchmark_execution.lane_workstream(descriptor["case_id"], descriptor["lane"]),
            task_kind="TEST",
            profile=descriptor["profile"],
            started_at=receipt["started_at"],
            finished_at=receipt["finished_at"],
            duration_seconds=receipt["duration_seconds"],
            counts=counts,
            validation={
                "ids": [descriptor["case_id"]],
                "exact_head": head,
                "evidence_state": "EXACT_HEAD",
                "outcome": "PASS",
            },
            terminal="PASS",
            budget={
                "soft_limit": None,
                "consumed": None,
                "unit": None,
                "state": "UNKNOWN",
                "disposition": "CONTINUE",
            },
            usage=usage if isinstance(usage, dict) else None,
            run_id=descriptor["run_id"],
            root=root,
        )
        efficiency_telemetry.write_record(root, record)
    except CaptureError as exc:
        return {"status": "BLOCK", "reason": exc.code, "execute_worker": False}
    except efficiency_telemetry.TelemetryError as exc:
        return {"status": "BLOCK", "reason": exc.code, "execute_worker": False}
    return {"status": "READY", "reason": "TELEMETRY_RECORDED", "execute_worker": False, "record": record}
