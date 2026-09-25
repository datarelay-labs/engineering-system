#!/usr/bin/env python3
"""Optional skills/hooks contract and verification-only permission authorize.

Production CLI surface is intentionally verification-only:
- ``check`` validates optional ``.engineering/skills.yaml``
- ``authorize`` verifies signed binding/dispatch assertions against
  host-administered trusted-adapter provenance

This tool does **not** expose key generation, bind, dispatch, or same-user
keyed-MAC minting commands. Those minting operations belong to a trusted
adapter/coordinator outside the coding-agent privilege boundary. Without
configured trusted external provenance, authorize fails closed as
``BOUNDARY_UNAVAILABLE``.
"""
from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from work_packet_authority import (  # noqa: E402
    AUTHORIZED_WORK_PACKET_PERMISSIONS,
    authorize_work_packet_author_permission,
)

DEFAULT_MAX_FINDINGS = 20
CONTRACT_REL = Path(".engineering") / "skills.yaml"
SCHEMA_REL = Path("schemas") / "skills-contract.schema.json"
# Documented caller-controlled env name that production authorize MUST ignore (round 5).
IGNORED_CALLER_TRUST_ANCHOR_ENV = "ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY"
# Backward-compatible alias for tests/behavior that still reference the old name.
TRUST_ANCHOR_ENV = IGNORED_CALLER_TRUST_ANCHOR_ENV
HOST_TRUST_ANCHOR_PATH = Path("/etc/engineering-system/skills-trust-anchor.pub")
HOST_REPLAY_STATE_PATH = Path("/etc/engineering-system/skills-replay-state")
# Fixed host verifier. Never resolved from PATH, environment, repo files, or CLI.
HOST_OPENSSL_PATH = Path("/usr/bin/openssl")

# Test-only library seams (never CLI/env/repo). Production authorize subprocess ignores these.
# ``_TEST_VERIFIER_AVAILABLE=False`` only forces the fixed verifier unavailable.
# It cannot select a different executable.
_TEST_TRUST_ANCHOR_PATH: Path | None = None
_TEST_VERIFIER_AVAILABLE: bool | None = None
_TEST_REPLAY_BOUNDARY_AVAILABLE: bool | None = None
_TEST_REPLAY_STORE: set[str] | None = None
_TEST_DISPATCH_RESERVATIONS: dict[str, dict[str, str]] | None = None
_REPLAY_LOCK = threading.Lock()

ACTION_CLASSES = frozenset(
    {
        "read",
        "repo_write",
        "shell",
        "network",
        "external_read",
        "external_write",
        "production_read",
        "production_write",
        "destructive",
    }
)
HOOK_NAMES = ("session_start", "pre_tool", "post_tool", "session_end")
HOOKS_EXECUTABLE = False
SCRIPTS_GRANT_EXECUTION = False
RESOURCES_EXECUTABLE = False
HIGH_RISK_CLASSES = frozenset({"external_write", "production_write", "destructive"})

DEFAULT_TOOL_REGISTRY: dict[str, frozenset[str]] = {
    "repo.read": frozenset({"read"}),
    "repo.write": frozenset({"read", "repo_write"}),
    "shell.local": frozenset({"shell"}),
    "shell.network": frozenset({"shell", "network", "external_read"}),
    "shell.external_write": frozenset(
        {"shell", "network", "external_read", "external_write"}
    ),
    "shell.production_write": frozenset(
        {"shell", "network", "external_read", "production_write", "destructive"}
    ),
    "network.fetch": frozenset({"network", "external_read"}),
    "network.post": frozenset({"network", "external_write"}),
    "production.read": frozenset({"production_read", "network", "external_read"}),
    "production.write": frozenset(
        {"production_write", "network", "external_write", "destructive"}
    ),
}


def _profile(
    allowed: Iterable[str],
    denied: Iterable[str] | None = None,
    require_approval: Iterable[str] | None = None,
) -> dict[str, frozenset[str]]:
    allowed_set = frozenset(allowed)
    denied_set = frozenset(denied) if denied is not None else (ACTION_CLASSES - allowed_set)
    approval_set = frozenset(require_approval or ())
    if allowed_set & denied_set or allowed_set | denied_set != ACTION_CLASSES:
        raise ValueError("invalid profile partition")
    if not approval_set <= allowed_set:
        raise ValueError("require_approval must be subset of allowed")
    return {
        "allowed": allowed_set,
        "denied": denied_set,
        "require_approval": approval_set,
    }


DEFAULT_PROFILES: dict[str, dict[str, frozenset[str]]] = {
    "repo_read": _profile(["read"]),
    "repo_write": _profile(["read", "repo_write", "shell"]),
    "external_read": _profile(
        ["read", "repo_write", "shell", "network", "external_read"]
    ),
    "external_write": _profile(
        ["read", "repo_write", "shell", "network", "external_read", "external_write"],
        require_approval=["external_write"],
    ),
    "production_read": _profile(
        ["read", "repo_write", "shell", "network", "external_read", "production_read"]
    ),
    "production_write": _profile(
        ACTION_CLASSES,
        denied=(),
        require_approval=["external_write", "production_write", "destructive"],
    ),
}


@dataclass(frozen=True)
class TrustedSessionBinding:
    profile: str
    policy_digest: str
    authority_permission: str
    approved_classes: frozenset[str]
    public_key_sha256: str


@dataclass(frozen=True)
class ActionRequest:
    tool_id: str
    classes: frozenset[str]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def fail_usage(message: str) -> None:
    raise SystemExit(f"SKILLS_CONTRACT=FAIL {message}")


def finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail[:180]}


def normalize_classes(raw: Any) -> frozenset[str] | None:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = [str(part).strip() for part in raw]
    else:
        return None
    if any(not item for item in items):
        return None
    return frozenset(items)


def profile_to_public(profile: dict[str, frozenset[str]]) -> dict[str, list[str]]:
    return {
        "allowed": sorted(profile["allowed"]),
        "denied": sorted(profile["denied"]),
        "require_approval": sorted(profile["require_approval"]),
    }


def _hook_payload(hooks: Any) -> dict[str, list[dict[str, str]]]:
    payload = {name: [] for name in HOOK_NAMES}
    if not isinstance(hooks, dict):
        return payload
    for name in HOOK_NAMES:
        entries = []
        for item in hooks.get(name) or []:
            if isinstance(item, dict):
                entries.append(
                    {"id": str(item.get("id") or ""), "kind": str(item.get("kind") or "")}
                )
            else:
                entries.append({"id": str(item), "kind": "invalid"})
        payload[name] = entries
    return payload


def _contract_authority_payload(
    profiles: dict[str, dict[str, frozenset[str]]],
    instance: dict | None,
) -> dict[str, Any]:
    skills_payload: list[dict[str, Any]] = []
    hooks_payload = {name: [] for name in HOOK_NAMES}
    if isinstance(instance, dict):
        for skill in instance.get("skills") or []:
            if not isinstance(skill, dict):
                continue
            skills_payload.append(
                {
                    "id": skill.get("id"),
                    "trigger": skill.get("trigger"),
                    "body": skill.get("body"),
                    "resources": list(skill.get("resources") or []),
                    "scripts": list(skill.get("scripts") or []),
                }
            )
        hooks_payload = _hook_payload(instance.get("hooks"))
    skills_payload.sort(key=lambda item: str(item.get("id") or ""))
    return {
        "hooks_executable": HOOKS_EXECUTABLE,
        "resources_executable": RESOURCES_EXECUTABLE,
        "scripts_grant_execution": SCRIPTS_GRANT_EXECUTION,
        "hooks": hooks_payload,
        "profiles": {
            name: profile_to_public(profiles[name]) for name in sorted(profiles)
        },
        "skills": skills_payload,
        "tools": {
            tool_id: sorted(classes)
            for tool_id, classes in sorted(DEFAULT_TOOL_REGISTRY.items())
        },
    }


def policy_digest(
    profiles: dict[str, dict[str, frozenset[str]]],
    instance: dict | None = None,
) -> str:
    encoded = json.dumps(
        _contract_authority_payload(profiles, instance),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_profile_mapping(raw: Any) -> dict[str, frozenset[str]] | None:
    if not isinstance(raw, dict) or set(raw) != {"allowed", "denied", "require_approval"}:
        return None
    allowed = normalize_classes(raw.get("allowed"))
    denied = normalize_classes(raw.get("denied"))
    approval = normalize_classes(raw.get("require_approval"))
    if allowed is None or denied is None or approval is None:
        return None
    if not allowed <= ACTION_CLASSES or not denied <= ACTION_CLASSES or not approval <= ACTION_CLASSES:
        return None
    if allowed & denied or allowed | denied != ACTION_CLASSES or not approval <= allowed:
        return None
    return {"allowed": allowed, "denied": denied, "require_approval": approval}


def is_narrowing(
    custom: dict[str, frozenset[str]],
    canonical: dict[str, frozenset[str]],
) -> bool:
    return (
        custom["allowed"] <= canonical["allowed"]
        and custom["denied"] >= canonical["denied"]
        and custom["require_approval"] >= canonical["require_approval"]
    )


def merge_profiles(custom: dict[str, Any] | None) -> tuple[dict[str, dict[str, frozenset[str]]], list[dict[str, str]]]:
    profiles = {name: dict(spec) for name, spec in DEFAULT_PROFILES.items()}
    findings: list[dict[str, str]] = []
    if not custom:
        return profiles, findings
    if not isinstance(custom, dict):
        return profiles, [finding("PROFILE", "profiles mapping required")]
    for name, raw in custom.items():
        if name not in DEFAULT_PROFILES:
            findings.append(finding("PROFILE", f"unknown profile {name}"))
            continue
        parsed = parse_profile_mapping(raw)
        if parsed is None:
            findings.append(finding("PROFILE", f"invalid profile {name}"))
            continue
        if not is_narrowing(parsed, DEFAULT_PROFILES[name]):
            findings.append(finding("PROFILE_BROADEN", name))
            continue
        profiles[name] = parsed
    return profiles, findings


def safe_repo_path(root: Path, raw: object) -> tuple[Path | None, str | None]:
    if not isinstance(raw, str) or not raw or raw.startswith(("/", "\\")) or "\\" in raw:
        return None, "UNSAFE_PATH"
    parts = Path(raw).parts
    if any(part in ("", ".", "..") for part in parts):
        return None, "UNSAFE_PATH"
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, "UNSAFE_PATH"
    return candidate, None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    body = {key: payload[key] for key in sorted(payload) if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_openssl_verifier() -> str:
    """Return the fixed host OpenSSL path, or empty when it is unavailable.

    Production resolution is only ``/usr/bin/openssl`` after root-owned,
    non-writable provenance checks. Caller PATH, environment, repository
    files, packet text, and CLI arguments are ignored. There is no fallback.
    """
    if _TEST_VERIFIER_AVAILABLE is False:
        return ""
    path = HOST_OPENSSL_PATH
    if not _host_path_provenance_ok(path, expect_file=True):
        return ""
    if not os.access(path, os.X_OK):
        return ""
    return str(path)


def ed25519_verify(public_key: Path, message: bytes, signature_b64: str) -> bool:
    openssl = resolve_openssl_verifier()
    if not openssl:
        return False
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "msg"
        sig = Path(tmp) / "sig"
        msg.write_bytes(message)
        sig.write_bytes(signature)
        completed = subprocess.run(
            [
                openssl,
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(public_key),
                "-rawin",
                "-in",
                str(msg),
                "-sigfile",
                str(sig),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return completed.returncode == 0


def verify_signed_json(path: Path, public_key: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "signature" not in payload:
        raise SystemExit("SKILLS_CONTRACT=FAIL signed artifact incomplete")
    if not ed25519_verify(public_key, canonical_payload_bytes(payload), str(payload["signature"])):
        raise SystemExit("SKILLS_CONTRACT=FAIL signature mismatch")
    return payload


def schema_findings(root: Path, instance: object) -> list[dict[str, str]]:
    schema_path = root / SCHEMA_REL
    if not schema_path.is_file():
        return [finding("SCHEMA", f"missing {SCHEMA_REL.as_posix()}")]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    return [finding("SCHEMA", error.message.replace("\n", " ")) for error in errors]


def skill_path_findings(root: Path, instance: dict) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    skills = instance.get("skills") or []
    if not isinstance(skills, list):
        return [finding("SKILL", "skills must be a list")]
    seen_ids: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            findings.append(finding("SKILL", "skill mapping required"))
            continue
        skill_id = str(skill.get("id") or "")
        if skill_id in seen_ids:
            findings.append(finding("SKILL", f"duplicate id {skill_id}"))
        seen_ids.add(skill_id)
        path, problem = safe_repo_path(root, skill.get("body"))
        if problem or path is None:
            findings.append(finding("PATH", f"{skill_id}:body"))
        elif not path.is_file():
            findings.append(finding("PATH_MISSING", f"{skill_id}:body"))
        for label, values in (
            ("resource", skill.get("resources") or []),
            ("script", skill.get("scripts") or []),
        ):
            for rel in values:
                path, problem = safe_repo_path(root, rel)
                if problem or path is None:
                    findings.append(finding("PATH", f"{skill_id}:{label}"))
                elif not path.is_file():
                    findings.append(finding("PATH_MISSING", f"{skill_id}:{label}"))
    return findings


def hook_findings(instance: dict) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    hooks = instance.get("hooks") or {}
    if not hooks:
        return findings
    if not isinstance(hooks, dict) or any(name not in HOOK_NAMES for name in hooks):
        return [finding("HOOK", "unknown hook name")]
    for name, entries in hooks.items():
        if not isinstance(entries, list):
            findings.append(finding("HOOK", f"{name} list required"))
            continue
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"id", "kind"}:
                findings.append(finding("HOOK", f"{name} fields"))
                continue
            if item.get("kind") != "metadata":
                findings.append(finding("HOOK_EXECUTABLE", f"{name}:{item.get('id')}"))
    return findings


def bind_session(
    *,
    profile: str,
    authority_permission: Any,
    policy_digest_value: str,
    approved_classes: Any = (),
    profiles: dict[str, dict[str, frozenset[str]]] | None = None,
    expected_digest: str | None = None,
    public_key_sha256: str = "",
) -> TrustedSessionBinding:
    permission = authorize_work_packet_author_permission(authority_permission)
    active = profiles or DEFAULT_PROFILES
    if profile not in active:
        raise SystemExit("SKILLS_CONTRACT=FAIL unknown profile")
    digest = expected_digest if expected_digest is not None else policy_digest(active)
    if policy_digest_value != digest:
        raise SystemExit("SKILLS_CONTRACT=FAIL policy digest mismatch")
    approved = normalize_classes(approved_classes)
    if approved is None or not approved <= ACTION_CLASSES:
        raise SystemExit("SKILLS_CONTRACT=FAIL invalid approved classes")
    if not isinstance(public_key_sha256, str) or len(public_key_sha256) != 64:
        raise SystemExit("SKILLS_CONTRACT=FAIL invalid public key hash")
    return TrustedSessionBinding(
        profile=profile,
        policy_digest=policy_digest_value,
        authority_permission=permission,
        approved_classes=approved,
        public_key_sha256=public_key_sha256,
    )


def action_request_for_tool(tool_id: str) -> ActionRequest:
    classes = DEFAULT_TOOL_REGISTRY.get(tool_id)
    if classes is None:
        raise SystemExit("SKILLS_CONTRACT=FAIL unknown tool")
    return ActionRequest(tool_id=tool_id, classes=frozenset(classes))


def evaluate_action(
    binding: TrustedSessionBinding,
    request: ActionRequest,
    *,
    profiles: dict[str, dict[str, frozenset[str]]] | None = None,
    expected_digest: str | None = None,
) -> Decision:
    active = profiles or DEFAULT_PROFILES
    digest = expected_digest if expected_digest is not None else policy_digest(active)
    if binding.policy_digest != digest:
        return Decision(False, "POLICY_DIGEST_MISMATCH")
    if binding.authority_permission not in AUTHORIZED_WORK_PACKET_PERMISSIONS:
        return Decision(False, "AUTHORITY_UNTRUSTED")
    if binding.profile not in active:
        return Decision(False, "UNKNOWN_PROFILE")
    if request.tool_id not in DEFAULT_TOOL_REGISTRY:
        return Decision(False, "UNKNOWN_TOOL")
    if request.classes != DEFAULT_TOOL_REGISTRY[request.tool_id]:
        return Decision(False, "CLASSIFICATION_TAMPER")
    if not request.classes:
        return Decision(False, "EMPTY_CLASSIFICATION")
    profile = active[binding.profile]
    for action_class in sorted(request.classes):
        if action_class in profile["denied"] or action_class not in profile["allowed"]:
            return Decision(False, "CLASS_DENIED")
        if (
            action_class in profile["require_approval"]
            and action_class not in binding.approved_classes
        ):
            return Decision(False, "APPROVAL_REQUIRED")
    return Decision(True, "ALLOW")


def reject_untrusted_override(payload: Any) -> Decision | None:
    if not isinstance(payload, dict):
        return None
    forbidden = {
        "profile",
        "approval",
        "approved",
        "approved_classes",
        "policy_digest",
        "authority_permission",
        "binding",
        "classes",
        "signature",
        "tool",
        "tool_id",
        "tool_classes",
        "private_key",
        "public_key",
        "trust_anchor",
    }
    if forbidden.intersection(payload):
        return Decision(False, "UNTRUSTED_OVERRIDE")
    return None


def load_contract_instance(root: Path) -> dict | None:
    contract_path = root / CONTRACT_REL
    if not contract_path.is_file():
        return None
    try:
        instance = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return instance if isinstance(instance, dict) else None


def check_contract(root: Path) -> dict[str, object]:
    contract_path = root / CONTRACT_REL
    if not contract_path.is_file():
        profiles = {name: dict(spec) for name, spec in DEFAULT_PROFILES.items()}
        return {
            "skills_contract": "ABSENT",
            "result": "PASS",
            "policy_digest": policy_digest(profiles, None),
            "profiles": {name: profile_to_public(spec) for name, spec in profiles.items()},
            "hooks_executable": HOOKS_EXECUTABLE,
            "scripts_grant_execution": SCRIPTS_GRANT_EXECUTION,
            "resources_executable": RESOURCES_EXECUTABLE,
            "findings": [],
        }
    try:
        instance = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return {
            "skills_contract": "PRESENT",
            "result": "FAIL",
            "policy_digest": "",
            "profiles": {},
            "hooks_executable": HOOKS_EXECUTABLE,
            "scripts_grant_execution": SCRIPTS_GRANT_EXECUTION,
            "resources_executable": RESOURCES_EXECUTABLE,
            "findings": [finding("SCHEMA", str(exc).splitlines()[0])],
        }
    findings = schema_findings(root, instance)
    profiles = {name: dict(spec) for name, spec in DEFAULT_PROFILES.items()}
    instance_dict = instance if isinstance(instance, dict) else None
    if isinstance(instance, dict) and not findings:
        findings.extend(skill_path_findings(root, instance))
        findings.extend(hook_findings(instance))
        merged, profile_findings = merge_profiles(instance.get("profiles"))
        findings.extend(profile_findings)
        profiles = merged
    ordered = sorted(findings, key=lambda item: (item["code"], item["detail"]))
    return {
        "skills_contract": "PRESENT",
        "result": "FAIL" if ordered else "PASS",
        "policy_digest": policy_digest(profiles, instance_dict) if not ordered else "",
        "profiles": {name: profile_to_public(spec) for name, spec in profiles.items()},
        "hooks_executable": HOOKS_EXECUTABLE,
        "scripts_grant_execution": SCRIPTS_GRANT_EXECUTION,
        "resources_executable": RESOURCES_EXECUTABLE,
        "findings": ordered,
    }


def load_effective_state(
    root: Path,
) -> tuple[dict[str, dict[str, frozenset[str]]], dict | None, str]:
    report = check_contract(root)
    if report["result"] != "PASS":
        raise SystemExit("SKILLS_CONTRACT=FAIL contract invalid")
    instance = load_contract_instance(root)
    custom = instance.get("profiles") if isinstance(instance, dict) else None
    profiles, findings = merge_profiles(custom)
    if findings:
        raise SystemExit("SKILLS_CONTRACT=FAIL profile merge failed")
    return profiles, instance, str(report["policy_digest"])


def canonical_request_sha256(request_payload: Any) -> str:
    payload = request_payload if isinstance(request_payload, dict) else {}
    body = {key: payload[key] for key in sorted(payload)}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


def _host_path_provenance_ok(path: Path, *, expect_file: bool) -> bool:
    """Require root-owned, non-group/world-writable host path (and parent)."""
    try:
        if expect_file:
            if not path.is_file():
                return False
        elif not path.exists():
            return False
        st = path.stat()
        if st.st_uid != 0:
            return False
        if st.st_mode & 0o022:
            return False
        parent = path.parent
        pst = parent.stat()
        if pst.st_uid != 0 or (pst.st_mode & 0o022):
            return False
    except OSError:
        return False
    return True


def resolve_trust_anchor() -> Path | None:
    """Resolve host-administered trust anchor only.

    Never reads caller environment, CLI args, or repository paths. The coding
    agent controlling ``ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY`` (or any other
    env var) cannot select the production trust anchor.
    """
    if _TEST_TRUST_ANCHOR_PATH is not None:
        path = Path(_TEST_TRUST_ANCHOR_PATH)
        return path.resolve() if path.is_file() else None
    path = HOST_TRUST_ANCHOR_PATH
    if not _host_path_provenance_ok(path, expect_file=True):
        return None
    return path.resolve()


def _safe_dispatch_token(dispatch_id: str) -> str | None:
    token = dispatch_id.strip()
    if not token or len(token) > 128:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in token):
        return None
    return token


def _effect_binding(effect_sha256: str, attempt_id: str) -> tuple[str, str] | None:
    effect = effect_sha256.strip().lower()
    attempt = _safe_dispatch_token(attempt_id)
    if attempt is None or len(effect) != 64 or any(ch not in "0123456789abcdef" for ch in effect):
        return None
    return effect, attempt


def _dispatch_marker(token: str) -> Path:
    return HOST_REPLAY_STATE_PATH / f"dispatch-{token}"


def _legacy_consumed_marker(token: str) -> Path:
    return HOST_REPLAY_STATE_PATH / f"consumed-{token}"


def _read_dispatch_record(path: Path) -> dict[str, str] | None:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        return {"state": "consumed"}
    try:
        raw = os.read(fd, 4096)
    finally:
        os.close(fd)
    text = raw.decode("utf-8", errors="replace")
    if text == "consumed\n":
        return {"state": "consumed", "effect_sha256": "", "attempt_id": ""}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"state": "consumed", "effect_sha256": "", "attempt_id": ""}
    if not isinstance(parsed, dict):
        return {"state": "consumed", "effect_sha256": "", "attempt_id": ""}
    state = str(parsed.get("state") or "")
    if state not in {"reserved", "consumed"}:
        return {"state": "consumed", "effect_sha256": "", "attempt_id": ""}
    return {
        "state": state,
        "effect_sha256": str(parsed.get("effect_sha256") or ""),
        "attempt_id": str(parsed.get("attempt_id") or ""),
    }


def _binding_matches(record: dict[str, str], effect: str, attempt: str) -> bool:
    return record.get("effect_sha256") == effect and record.get("attempt_id") == attempt


def _test_reservations() -> dict[str, dict[str, str]] | None:
    global _TEST_DISPATCH_RESERVATIONS
    if _TEST_REPLAY_BOUNDARY_AVAILABLE is not True or _TEST_REPLAY_STORE is None:
        return None
    if _TEST_DISPATCH_RESERVATIONS is None:
        _TEST_DISPATCH_RESERVATIONS = {}
    return _TEST_DISPATCH_RESERVATIONS


def _production_replay_ready() -> bool:
    return _host_path_provenance_ok(HOST_REPLAY_STATE_PATH, expect_file=False) and HOST_REPLAY_STATE_PATH.is_dir()


def consume_dispatch_once(dispatch_id: str) -> str:
    """Atomically consume a high-risk dispatch_id for one-time authorize.

    Returns ``ok``, ``replay``, or ``unavailable``. Path existence alone is not
    enough: production requires an exclusive create under the host replay-state
    directory. If that trusted consume primitive cannot run, return unavailable
    (caller gets ``BOUNDARY_UNAVAILABLE``) rather than a repo-local nonce store.
    A dispatch already reserved for an effect is taken and is not consumed again.
    """
    token = _safe_dispatch_token(dispatch_id)
    if token is None:
        return "unavailable"

    if _TEST_REPLAY_BOUNDARY_AVAILABLE is False:
        return "unavailable"
    if _TEST_REPLAY_BOUNDARY_AVAILABLE is True:
        store = _TEST_REPLAY_STORE
        reservations = _test_reservations()
        if store is None or reservations is None:
            return "unavailable"
        with _REPLAY_LOCK:
            if token in store or token in reservations:
                return "replay"
            store.add(token)
            reservations[token] = {"state": "consumed", "effect_sha256": "", "attempt_id": ""}
        return "ok"

    if not _production_replay_ready():
        return "unavailable"
    if _legacy_consumed_marker(token).exists() or _dispatch_marker(token).exists():
        return "replay"
    marker = _dispatch_marker(token)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, b'{"state":"consumed"}\n')
        finally:
            os.close(fd)
        return "ok"
    except FileExistsError:
        return "replay"
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return "replay"
        return "unavailable"


def reserve_dispatch(dispatch_id: str, effect_sha256: str, attempt_id: str) -> str:
    """Atomically reserve one dispatch for one effect digest and executor attempt.

    Returns ``reserved``, ``owned_reserved``, ``owned_consumed``, ``conflict``,
    or ``unavailable``. Another effect or attempt cannot take the reservation.
    """
    token = _safe_dispatch_token(dispatch_id)
    binding = _effect_binding(effect_sha256, attempt_id)
    if token is None or binding is None:
        return "unavailable"
    effect, attempt = binding

    if _TEST_REPLAY_BOUNDARY_AVAILABLE is False:
        return "unavailable"
    if _TEST_REPLAY_BOUNDARY_AVAILABLE is True:
        store = _TEST_REPLAY_STORE
        reservations = _test_reservations()
        if store is None or reservations is None:
            return "unavailable"
        with _REPLAY_LOCK:
            current = reservations.get(token)
            if token in store:
                if current is not None and current.get("state") == "consumed" and _binding_matches(current, effect, attempt):
                    return "owned_consumed"
                return "conflict"
            if current is None:
                reservations[token] = {
                    "state": "reserved",
                    "effect_sha256": effect,
                    "attempt_id": attempt,
                }
                return "reserved"
            if _binding_matches(current, effect, attempt):
                return "owned_consumed" if current.get("state") == "consumed" else "owned_reserved"
            return "conflict"

    if not _production_replay_ready():
        return "unavailable"
    if _legacy_consumed_marker(token).exists():
        return "conflict"
    payload = json.dumps(
        {"state": "reserved", "effect_sha256": effect, "attempt_id": attempt},
        separators=(",", ":"),
    ).encode("utf-8")
    marker = _dispatch_marker(token)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return "reserved"
    except FileExistsError:
        record = _read_dispatch_record(marker)
        if record is None:
            return "unavailable"
        if _binding_matches(record, effect, attempt):
            return "owned_consumed" if record.get("state") == "consumed" else "owned_reserved"
        return "conflict"
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return "conflict"
        return "unavailable"


def finalize_dispatch(dispatch_id: str, effect_sha256: str, attempt_id: str) -> str:
    """Mark an owned reservation consumed. A different owner cannot finalize it."""
    token = _safe_dispatch_token(dispatch_id)
    binding = _effect_binding(effect_sha256, attempt_id)
    if token is None or binding is None:
        return "unavailable"
    effect, attempt = binding

    if _TEST_REPLAY_BOUNDARY_AVAILABLE is False:
        return "unavailable"
    if _TEST_REPLAY_BOUNDARY_AVAILABLE is True:
        store = _TEST_REPLAY_STORE
        reservations = _test_reservations()
        if store is None or reservations is None:
            return "unavailable"
        with _REPLAY_LOCK:
            current = reservations.get(token)
            if current is None or not _binding_matches(current, effect, attempt):
                return "conflict"
            if token in store and current.get("state") != "consumed":
                return "conflict"
            current["state"] = "consumed"
            store.add(token)
        return "ok"

    if not _production_replay_ready():
        return "unavailable"
    marker = _dispatch_marker(token)
    try:
        fd = os.open(str(marker), os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "conflict"
    except OSError:
        return "unavailable"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 4096)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return "conflict"
        if not isinstance(parsed, dict):
            return "conflict"
        record = {
            "state": str(parsed.get("state") or ""),
            "effect_sha256": str(parsed.get("effect_sha256") or ""),
            "attempt_id": str(parsed.get("attempt_id") or ""),
        }
        if not _binding_matches(record, effect, attempt):
            return "conflict"
        if record["state"] == "consumed":
            return "ok"
        if record["state"] != "reserved":
            return "conflict"
        payload = json.dumps(
            {"state": "consumed", "effect_sha256": effect, "attempt_id": attempt},
            separators=(",", ":"),
        ).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)
        os.fsync(fd)
        return "ok"
    except OSError:
        return "unavailable"
    finally:
        os.close(fd)


def authorize(
    root: Path,
    *,
    binding_assertion: Path,
    dispatch_assertion: Path,
    request_json: str = "",
    consume_replay: bool = True,
) -> Decision:
    """Verification-only authorization against a host-administered trust anchor.

    ``consume_replay`` defaults to consuming a high-risk dispatch id. The watch
    host passes False, then consumes only after a durable effect receipt.
    """
    if not sys.platform.startswith("linux"):
        return Decision(False, "BOUNDARY_UNAVAILABLE")
    if not resolve_openssl_verifier():
        return Decision(False, "BOUNDARY_UNAVAILABLE")
    anchor = resolve_trust_anchor()
    if anchor is None:
        return Decision(False, "BOUNDARY_UNAVAILABLE")
    request_payload: dict[str, Any] = {}
    if request_json:
        try:
            parsed = json.loads(request_json)
        except json.JSONDecodeError as exc:
            fail_usage(f"request json: {exc}")
        if not isinstance(parsed, dict):
            return Decision(False, "REQUEST_INVALID")
        override = reject_untrusted_override(parsed)
        if override is not None:
            return override
        request_payload = parsed
    if not binding_assertion.is_file() or not dispatch_assertion.is_file():
        return Decision(False, "ASSERTION_MISSING")
    try:
        binding_payload = verify_signed_json(binding_assertion, anchor)
        dispatch_payload = verify_signed_json(dispatch_assertion, anchor)
    except SystemExit as exc:
        if "signature mismatch" in str(exc):
            return Decision(False, "SIGNATURE_MISMATCH")
        raise
    anchor_hash = sha256_file(anchor)
    if binding_payload.get("public_key_sha256") != anchor_hash:
        return Decision(False, "TRUST_ANCHOR_MISMATCH")
    if dispatch_payload.get("binding_public_key_sha256") != anchor_hash:
        return Decision(False, "TRUST_ANCHOR_MISMATCH")
    expected_request_hash = canonical_request_sha256(request_payload)
    actual_request_hash = dispatch_payload.get("request_sha256")
    if not isinstance(actual_request_hash, str) or actual_request_hash != expected_request_hash:
        return Decision(False, "REQUEST_BINDING_MISMATCH")
    classes = dispatch_payload.get("classes") or []
    if not isinstance(classes, list):
        return Decision(False, "CLASSIFICATION_TAMPER")
    class_set = frozenset(str(item) for item in classes)
    if class_set & HIGH_RISK_CLASSES:
        dispatch_id = dispatch_payload.get("dispatch_id")
        expires = dispatch_payload.get("expires_at_unix")
        if not isinstance(dispatch_id, str) or not dispatch_id.strip():
            return Decision(False, "REPLAY_CONTRACT_INCOMPLETE")
        if not isinstance(expires, int) or expires <= 0:
            return Decision(False, "REPLAY_CONTRACT_INCOMPLETE")
        if int(time.time()) >= expires:
            return Decision(False, "DISPATCH_EXPIRED")
        if consume_replay:
            consumed = consume_dispatch_once(str(dispatch_id))
            if consumed == "unavailable":
                return Decision(False, "BOUNDARY_UNAVAILABLE")
            if consumed == "replay":
                return Decision(False, "REPLAY")
            if consumed != "ok":
                return Decision(False, "BOUNDARY_UNAVAILABLE")
    profiles, _, digest = load_effective_state(root)
    if binding_payload.get("policy_digest") != digest:
        return Decision(False, "POLICY_DIGEST_MISMATCH")
    if dispatch_payload.get("policy_digest") != digest:
        return Decision(False, "POLICY_DIGEST_MISMATCH")
    binding = bind_session(
        profile=str(binding_payload.get("profile") or ""),
        authority_permission=binding_payload.get("authority_permission"),
        policy_digest_value=str(binding_payload.get("policy_digest") or ""),
        approved_classes=binding_payload.get("approved_classes", ()),
        profiles=profiles,
        expected_digest=digest,
        public_key_sha256=str(binding_payload.get("public_key_sha256") or ""),
    )
    tool_id = str(dispatch_payload.get("tool_id") or "")
    request = action_request_for_tool(tool_id)
    if sorted(request.classes) != sorted(class_set):
        return Decision(False, "CLASSIFICATION_TAMPER")
    return evaluate_action(binding, request, profiles=profiles, expected_digest=digest)


def emit_check(root: Path, report: dict[str, object], max_findings: int, mode: str) -> None:
    if mode == "raw":
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    findings = list(report["findings"])
    shown = findings if mode == "full" else findings[:max_findings]
    print(f"SKILLS_CONTRACT={report['skills_contract']}")
    print(f"RESULT={report['result']}")
    print(f"HOOKS_EXECUTABLE={str(report['hooks_executable']).lower()}")
    print(f"SCRIPTS_GRANT_EXECUTION={str(report['scripts_grant_execution']).lower()}")
    print(f"RESOURCES_EXECUTABLE={str(report['resources_executable']).lower()}")
    if report["policy_digest"]:
        print(f"POLICY_DIGEST={report['policy_digest']}")
    print(f"FINDINGS={len(findings)}")
    if mode != "full" and len(findings) > max_findings:
        print("FINDINGS_TRUNCATED=YES")
    for item in shown:
        print(f"FINDING={item['code']}:{item['detail']}")
    command = "python3 tools/skills-contract.py check --full"
    if root.resolve() != Path.cwd().resolve():
        command += " --root " + shlex.quote(str(root))
    print(f"FULLER={command}")
    print(f"RAW={command.replace('--full', '--raw', 1)}")


def emit_authorize(decision: Decision) -> None:
    print(f"DECISION={'ALLOW' if decision.allowed else 'DENY'}")
    print(f"REASON={decision.reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Validate optional skills contract")
    check.add_argument("--root", type=Path, default=Path.cwd())
    check.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    check.add_argument("--full", action="store_true")
    check.add_argument("--raw", action="store_true")

    authorize_cmd = sub.add_parser(
        "authorize",
        help="Verification-only authorize using host-administered trust-anchor pubkey",
    )
    authorize_cmd.add_argument("--root", type=Path, default=Path.cwd())
    authorize_cmd.add_argument("--binding-assertion", type=Path, required=True)
    authorize_cmd.add_argument("--dispatch-assertion", type=Path, required=True)
    authorize_cmd.add_argument("--request-json", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()

    if args.command == "check":
        if args.full and args.raw:
            fail_usage("choose --full or --raw")
        mode = "raw" if args.raw else "full" if args.full else "default"
        report = check_contract(root)
        emit_check(root, report, max(1, args.max_findings), mode)
        return 0 if report["result"] == "PASS" else 1

    if args.command == "authorize":
        decision = authorize(
            root,
            binding_assertion=args.binding_assertion,
            dispatch_assertion=args.dispatch_assertion,
            request_json=args.request_json,
        )
        emit_authorize(decision)
        return 0 if decision.allowed else 1

    fail_usage("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
