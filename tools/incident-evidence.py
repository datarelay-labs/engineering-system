#!/usr/bin/env python3
"""Incident Packet check and bounded read-only core evidence capture.

The packet records incident control state. It never grants production,
destructive, or mitigation authority. SAFETY_FREEZE=ON only narrows execution.

Capture reads fixed Git metadata, Linux memory/swap/load/PSI facts, and the
aggregate Cursor persistent-session count. It writes one JSON artifact inside
the repository Git directory and does not call GitHub, a model, a project
runtime command, or any mutating service command. It does not stop or mutate
Cursor sessions.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "incident-evidence.schema.json"

MAX_PACKET_BYTES = 64 * 1024
MAX_CAPTURES_PER_INCIDENT = 32
MAX_INCIDENT_BYTES = 256 * 1024
MAX_RECORD_BYTES = 8 * 1024
MAX_STATUS_BYTES = 256 * 1024
MAX_TEXT_BYTES = 64 * 1024

LOW_MEM_AVAILABLE_BYTES = 1024**3
MATERIAL_SWAP_USED_BYTES = 1024**3
ELEVATED_PSI_AVG10 = 10.0
HIGH_SESSION_COUNT = 8

INCIDENT_ID_RE = re.compile(r"^INC-[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*$")
CAPTURE_ID_RE = re.compile(r"^CAP-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
TARGET_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._@+/-]{1,200}$")
HEADER_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
PSI_LINE_RE = re.compile(
    r"^(some|full) avg10=(\d+(?:\.\d+)?) avg60=\d+(?:\.\d+)? avg300=\d+(?:\.\d+)? total=\d+\s*$"
)
OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

REQUIRED_HEADERS = (
    "INCIDENT_PACKET_VERSION",
    "TARGET_REPO",
    "INCIDENT_ID",
    "INCIDENT_PHASE",
    "SEVERITY",
    "IMPACT_STATE",
    "DETECTED_AT",
    "LAST_UPDATED_AT",
    "INCIDENT_COMMANDER",
    "OPS_OWNER",
    "COMMS_OWNER",
    "SAFETY_FREEZE",
    "LAST_EVIDENCE_CAPTURE",
)
GRANT_KEYS = frozenset(
    {
        "AUTHORITY",
        "AUTHORIZED",
        "GRANT",
        "EXECUTION_GRANT",
        "APPROVED_MUTATION",
        "AUTHORIZE_MITIGATION",
        "PRODUCTION_MUTATION",
        "DESTRUCTIVE_MUTATION",
    }
)
PHASES = frozenset({"DETECTED", "STABILIZING", "MITIGATED", "MONITORING", "RESOLVED"})
SEVERITIES = frozenset({"UNKNOWN", "CRITICAL", "HIGH", "MEDIUM", "LOW"})
IMPACTS = frozenset({"UNKNOWN", "ONGOING", "CONTAINED", "ENDED"})
PHASE_IMPACTS = {
    "DETECTED": frozenset({"UNKNOWN", "ONGOING", "CONTAINED"}),
    "STABILIZING": frozenset({"UNKNOWN", "ONGOING", "CONTAINED"}),
    "MITIGATED": frozenset({"CONTAINED", "ENDED"}),
    "MONITORING": frozenset({"CONTAINED", "ENDED"}),
    "RESOLVED": frozenset({"ENDED"}),
}
SECTIONS = (
    "Current Impact",
    "Stabilization / Safety State",
    "Evidence",
    "Current Hypothesis",
    "Mitigation / Rollback",
    "Next Update / Blocker",
    "Corrective Follow-ups",
)
PRESSURE_ORDER = (
    "LOW_MEM_AVAILABLE",
    "MATERIAL_SWAP_USE",
    "ELEVATED_MEMORY_PRESSURE",
    "ELEVATED_IO_PRESSURE",
    "HIGH_PERSISTENT_SESSION_COUNT",
)


class EvidenceBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_record(record: dict, schema: dict | None = None) -> list[str]:
    validator = Draft202012Validator(schema or load_schema())
    errors = sorted(validator.iter_errors(record), key=lambda item: list(item.path))
    rendered = []
    for error in errors:
        where = ".".join(str(part) for part in error.path) or "<root>"
        rendered.append(f"{where}: {error.message}")
    return rendered


def load_preflight():
    name = "cursor_resource_preflight"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).with_name("cursor-resource-preflight.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EvidenceBlocked("PREFLIGHT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def emit(lines: dict[str, str]) -> str:
    order = (
        "RESULT",
        "REASON",
        "AUTHORITY",
        "SAFETY_FREEZE",
        "SAFETY_EFFECT",
        "INCIDENT_ID",
        "INCIDENT_PHASE",
        "OVERALL",
        "CAPTURE_ID",
        "EVIDENCE_REF",
        "PRESSURE_FACTS",
        "ROOT_CAUSE",
    )
    rendered = [f"{key}={lines[key]}" for key in order if key in lines]
    return "\n".join(rendered) + "\n"


def fail_packet(reason: str) -> tuple[str, int]:
    return emit({"RESULT": "FAIL", "REASON": reason, "AUTHORITY": "NONE"}), 2


def block_capture(reason: str) -> tuple[str, int]:
    return (
        emit(
            {
                "RESULT": "BLOCK",
                "REASON": reason,
                "AUTHORITY": "NONE",
                "ROOT_CAUSE": "UNPROVEN",
            }
        ),
        2,
    )


def parse_timestamp(value: str) -> datetime | None:
    if not TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def check_packet_text(text: str) -> tuple[str, int]:
    if len(text.encode("utf-8")) > MAX_PACKET_BYTES:
        return fail_packet("PACKET_TOO_LARGE")
    header_lines: list[str] = []
    body_lines: list[str] = []
    in_body = False
    for line in text.splitlines():
        if not in_body and (not line.strip() or line.startswith("#")):
            if line.startswith("#"):
                in_body = True
                body_lines.append(line)
            elif header_lines:
                in_body = True
            continue
        if in_body:
            body_lines.append(line)
        else:
            header_lines.append(line)
    headers: dict[str, str] = {}
    for line in header_lines:
        match = HEADER_RE.fullmatch(line.strip())
        if not match:
            return fail_packet("MALFORMED_HEADER")
        key, value = match.group(1), match.group(2)
        if key in GRANT_KEYS:
            return fail_packet("AUTHORITY_GRANT")
        if key in headers:
            return fail_packet(f"DUPLICATE_HEADER:{key}")
        if key not in REQUIRED_HEADERS:
            return fail_packet(f"UNKNOWN_HEADER:{key}")
        headers[key] = value
    for line in body_lines:
        match = HEADER_RE.fullmatch(line.strip())
        if match and match.group(1) in GRANT_KEYS:
            return fail_packet("AUTHORITY_GRANT")
    for key in REQUIRED_HEADERS:
        if key not in headers:
            return fail_packet(f"MISSING_HEADER:{key}")
    if headers["INCIDENT_PACKET_VERSION"] != "1":
        return fail_packet("UNKNOWN_VALUE:INCIDENT_PACKET_VERSION")
    if not TARGET_REPO_RE.fullmatch(headers["TARGET_REPO"]):
        return fail_packet("UNKNOWN_VALUE:TARGET_REPO")
    if not _valid_incident_id(headers["INCIDENT_ID"]):
        return fail_packet("UNKNOWN_VALUE:INCIDENT_ID")
    if headers["INCIDENT_PHASE"] not in PHASES:
        return fail_packet("UNKNOWN_VALUE:INCIDENT_PHASE")
    if headers["SEVERITY"] not in SEVERITIES:
        return fail_packet("UNKNOWN_VALUE:SEVERITY")
    if headers["IMPACT_STATE"] not in IMPACTS:
        return fail_packet("UNKNOWN_VALUE:IMPACT_STATE")
    detected = parse_timestamp(headers["DETECTED_AT"])
    updated = parse_timestamp(headers["LAST_UPDATED_AT"])
    if detected is None:
        return fail_packet("UNKNOWN_VALUE:DETECTED_AT")
    if updated is None:
        return fail_packet("UNKNOWN_VALUE:LAST_UPDATED_AT")
    if updated < detected:
        return fail_packet("UPDATED_BEFORE_DETECTED")
    if headers["IMPACT_STATE"] not in PHASE_IMPACTS[headers["INCIDENT_PHASE"]]:
        return fail_packet("PHASE_IMPACT_CONTRADICTION")
    if not ACTOR_RE.fullmatch(headers["INCIDENT_COMMANDER"]):
        return fail_packet("UNKNOWN_VALUE:INCIDENT_COMMANDER")
    if headers["OPS_OWNER"] != "SAME_AS_IC" and not ACTOR_RE.fullmatch(headers["OPS_OWNER"]):
        return fail_packet("UNKNOWN_VALUE:OPS_OWNER")
    if headers["COMMS_OWNER"] not in {"SAME_AS_IC", "N/A"} and not ACTOR_RE.fullmatch(
        headers["COMMS_OWNER"]
    ):
        return fail_packet("UNKNOWN_VALUE:COMMS_OWNER")
    if headers["SAFETY_FREEZE"] not in {"ON", "OFF", "N/A"}:
        return fail_packet("UNKNOWN_VALUE:SAFETY_FREEZE")
    capture = headers["LAST_EVIDENCE_CAPTURE"]
    if capture != "NONE" and not CAPTURE_ID_RE.fullmatch(capture):
        return fail_packet("UNKNOWN_VALUE:LAST_EVIDENCE_CAPTURE")
    section_error = _check_sections(body_lines)
    if section_error:
        return fail_packet(section_error)
    safety = headers["SAFETY_FREEZE"]
    return (
        emit(
            {
                "RESULT": "PASS",
                "AUTHORITY": "NONE",
                "SAFETY_FREEZE": safety,
                "SAFETY_EFFECT": "NARROW" if safety == "ON" else "NONE",
                "INCIDENT_ID": headers["INCIDENT_ID"],
                "INCIDENT_PHASE": headers["INCIDENT_PHASE"],
            }
        ),
        0,
    )


def _check_sections(lines: list[str]) -> str | None:
    found: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_name is not None:
                found.append((current_name, current))
            current_name = line[3:].strip()
            current = []
            continue
        if current_name is None and line.strip():
            return "MALFORMED_SECTION"
        if current_name is not None:
            current.append(line)
    if current_name is not None:
        found.append((current_name, current))
    names = [name for name, _body in found]
    if names != list(SECTIONS):
        missing = [name for name in SECTIONS if name not in names]
        if missing:
            return f"MISSING_SECTION:{missing[0]}"
        unknown = [name for name in names if name not in SECTIONS]
        if unknown:
            return f"UNKNOWN_SECTION:{unknown[0]}"
        return "SECTION_ORDER"
    for name, body in found:
        if not "\n".join(body).strip():
            return f"EMPTY_SECTION:{name}"
    return None


def _valid_incident_id(value: str) -> bool:
    return len(value) <= 80 and INCIDENT_ID_RE.fullmatch(value) is not None


def _source(state: str, reason: str, **fields: object) -> dict[str, object]:
    payload: dict[str, object] = {"state": state, "reason": reason}
    if state == "CAPTURED":
        payload.update(fields)
    return payload


def _read_bounded(path: Path, limit: int) -> tuple[str | None, str | None]:
    try:
        data = path.read_bytes()
    except OSError:
        return None, "ABSENT"
    if len(data) > limit:
        return None, "UNBOUNDED"
    try:
        return data.decode("utf-8"), None
    except UnicodeError:
        return None, "UNPARSEABLE"


def parse_psi(text: str) -> dict[str, float] | None:
    some = None
    full = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = PSI_LINE_RE.fullmatch(line)
        if not match:
            return None
        value = round(float(match.group(2)), 2)
        if match.group(1) == "some":
            some = value
        else:
            full = value
    if some is None or full is None:
        return None
    return {"some_avg10": some, "full_avg10": full}


def normalize_remote(url: str) -> str | None:
    raw = url.strip()
    if not raw or any(char in raw for char in "\n\r\x00"):
        return None
    host = ""
    path = ""
    if raw.startswith("git@") and ":" in raw[4:]:
        host, path = raw[4:].split(":", 1)
    elif "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        path = parsed.path
    else:
        return None
    if not host or "/" in host or "\\" in host:
        return None
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return None
    if parts[-1].endswith(".git"):
        parts[-1] = parts[-1][: -len(".git")]
    if len(parts) != 2:
        return None
    owner, name = parts
    if not OWNER_RE.fullmatch(owner) or not OWNER_RE.fullmatch(name):
        return None
    return f"{owner}/{name}"


def count_porcelain_z(blob: bytes) -> int:
    count = 0
    index = 0
    while index < len(blob):
        if index + 3 > len(blob):
            break
        status = blob[index : index + 2]
        nul = blob.find(b"\0", index)
        if nul < 0:
            break
        count += 1
        index = nul + 1
        if b"R" in status or b"C" in status:
            nul = blob.find(b"\0", index)
            if nul < 0:
                break
            index = nul + 1
    return count


def _git_argv(root: Path, args: list[str]) -> list[str]:
    # Command-line config overrides repository core.fsmonitor so status cannot
    # execute a repository-selected hook. The value false disables the feature.
    return ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args]


def _run_git(root: Path, args: list[str], binary: bool = False):
    try:
        return subprocess.run(
            _git_argv(root, args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            text=not binary,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvidenceBlocked("GIT_TIMEOUT") from exc
    except OSError as exc:
        raise EvidenceBlocked("GIT_UNAVAILABLE") from exc


def _git_identity(root: Path) -> tuple[dict[str, object], dict[str, object], Path]:
    if not root.is_dir():
        raise EvidenceBlocked("ROOT_UNAVAILABLE")
    inside = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise EvidenceBlocked("NOT_A_GIT_REPOSITORY")
    git_dir_run = _run_git(root, ["rev-parse", "--absolute-git-dir"])
    head_run = _run_git(root, ["rev-parse", "HEAD"])
    if git_dir_run.returncode != 0 or head_run.returncode != 0:
        raise EvidenceBlocked("HEAD_UNAVAILABLE")
    head = head_run.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise EvidenceBlocked("HEAD_UNAVAILABLE")
    git_dir = Path(git_dir_run.stdout.strip()).resolve()
    if not git_dir.is_dir():
        raise EvidenceBlocked("GIT_DIR_UNRESOLVED")
    ref_run = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    ref_name = ref_run.stdout.strip() if ref_run.returncode == 0 else ""
    detached = ref_name == "HEAD"
    branch_safe = (not detached) and BRANCH_RE.fullmatch(ref_name) is not None and ".." not in ref_name
    try:
        status = subprocess.run(
            _git_argv(root, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvidenceBlocked("GIT_TIMEOUT") from exc
    except OSError as exc:
        raise EvidenceBlocked("GIT_UNAVAILABLE") from exc
    status_ok = status.returncode == 0
    status_bounded = status_ok and len(status.stdout) <= MAX_STATUS_BYTES
    git: dict[str, object] = {
        "state": "CAPTURED",
        "head": head,
        "ref_state": "detached" if detached else "branch",
    }
    if not detached and branch_safe:
        git["branch"] = ref_name
    if status_bounded:
        count = count_porcelain_z(status.stdout)
        git["dirty"] = count > 0
        git["changed_file_count"] = count
    elif status_ok:
        git["dirty"] = len(status.stdout) > 0
    if status_bounded and (detached or branch_safe):
        git["reason"] = "OK"
    elif not status_ok and (detached or branch_safe):
        git["reason"] = "GIT_STATUS_UNAVAILABLE"
    elif status_bounded and not branch_safe and not detached:
        git["reason"] = "BRANCH_REDACTED"
    elif not status_bounded and (not branch_safe and not detached):
        git["reason"] = "GIT_PARTIAL"
        git.pop("branch", None)
    else:
        git["reason"] = "CHANGED_FILE_COUNT_UNBOUNDED"
        git.pop("changed_file_count", None)
    origin = _run_git(root, ["remote", "get-url", "origin"])
    if origin.returncode != 0:
        repository = _source("UNAVAILABLE", "ORIGIN_ABSENT")
    else:
        identity = normalize_remote(origin.stdout)
        if identity is None:
            repository = _source("REDACTED", "REMOTE_URL_UNPARSEABLE")
        else:
            repository = _source("CAPTURED", "OK", identity=identity)
    return repository, git, git_dir


def _host_platform() -> tuple[str, str]:
    name = sys.platform.lower()
    if name.startswith("linux"):
        return "linux", "SUPPORTED"
    if re.fullmatch(r"[a-z0-9._-]{1,32}", name):
        return name, "UNSUPPORTED"
    return "unknown", "UNSUPPORTED"


def _memory_and_swap(text: str | None, problem: str | None, supported: bool) -> tuple[dict, dict]:
    if not supported and text is None:
        return _source("UNSUPPORTED", "PLATFORM_UNSUPPORTED"), _source("UNSUPPORTED", "PLATFORM_UNSUPPORTED")
    if problem == "ABSENT" or text is None and problem is None:
        return _source("UNAVAILABLE", "MEMINFO_ABSENT"), _source("UNAVAILABLE", "MEMINFO_ABSENT")
    if problem == "UNBOUNDED":
        return _source("UNAVAILABLE", "MEMINFO_UNBOUNDED"), _source("UNAVAILABLE", "MEMINFO_UNBOUNDED")
    if problem == "UNPARSEABLE" or text is None:
        return _source("UNAVAILABLE", "MEMINFO_UNPARSEABLE"), _source("UNAVAILABLE", "MEMINFO_UNPARSEABLE")
    try:
        parsed = load_preflight().parse_meminfo(text)
    except Exception as exc:
        reason = getattr(exc, "reason", "")
        if "missing" in reason:
            code = "MEMINFO_INCOMPLETE"
        elif "negative" in reason or "not positive" in reason:
            code = "MEMINFO_INVALID"
        else:
            code = "MEMINFO_UNPARSEABLE"
        return _source("UNAVAILABLE", code), _source("UNAVAILABLE", code)
    swap_total = parsed["SwapTotal"]
    swap_free = parsed.get("SwapFree", 0)
    swap_used = swap_total - swap_free
    if swap_used < 0:
        return (
            _source(
                "CAPTURED",
                "OK",
                total_bytes=parsed["MemTotal"],
                available_bytes=parsed["MemAvailable"],
            ),
            _source("UNAVAILABLE", "SWAP_INVALID"),
        )
    memory = _source(
        "CAPTURED",
        "OK",
        total_bytes=parsed["MemTotal"],
        available_bytes=parsed["MemAvailable"],
    )
    swap = _source(
        "CAPTURED",
        "OK",
        configured=swap_total > 0,
        total_bytes=swap_total,
        used_bytes=swap_used,
    )
    return memory, swap


def _load(text: str | None, problem: str | None) -> dict:
    if problem == "UNBOUNDED":
        return _source("UNAVAILABLE", "LOAD_UNBOUNDED")
    if problem == "UNPARSEABLE":
        return _source("UNAVAILABLE", "LOAD_UNPARSEABLE")
    if text is None and problem == "ABSENT":
        return _source("UNAVAILABLE", "LOAD_UNAVAILABLE")
    if text is None:
        try:
            one, five, fifteen = os.getloadavg()
        except OSError:
            return _source("UNAVAILABLE", "LOAD_UNAVAILABLE")
        return _source(
            "CAPTURED",
            "OK",
            avg_1m=round(one, 2),
            avg_5m=round(five, 2),
            avg_15m=round(fifteen, 2),
        )
    parts = text.split()
    if len(parts) != 3:
        return _source("UNAVAILABLE", "LOAD_UNPARSEABLE")
    try:
        values = [round(float(part), 2) for part in parts]
    except ValueError:
        return _source("UNAVAILABLE", "LOAD_UNPARSEABLE")
    if any(value < 0 for value in values):
        return _source("UNAVAILABLE", "LOAD_UNPARSEABLE")
    return _source("CAPTURED", "OK", avg_1m=values[0], avg_5m=values[1], avg_15m=values[2])


def _psi(text: str | None, problem: str | None, supported: bool, absent: str, bad: str, unbounded: str) -> dict:
    if not supported and text is None:
        return _source("UNSUPPORTED", "PLATFORM_UNSUPPORTED")
    if problem == "ABSENT" or text is None:
        return _source("UNAVAILABLE", absent)
    if problem == "UNBOUNDED":
        return _source("UNAVAILABLE", unbounded)
    if problem == "UNPARSEABLE":
        return _source("UNAVAILABLE", bad)
    parsed = parse_psi(text)
    if parsed is None:
        return _source("UNAVAILABLE", bad)
    return _source("CAPTURED", "OK", **parsed)


def _sessions(persist_text: str | None, problem: str | None) -> dict:
    if problem == "UNBOUNDED":
        return _source("UNAVAILABLE", "SESSION_LIST_UNBOUNDED")
    if problem == "UNPARSEABLE":
        return _source("UNAVAILABLE", "SESSION_LIST_UNPARSEABLE")
    try:
        preflight = load_preflight()
    except Exception:
        return _source("UNAVAILABLE", "PREFLIGHT_UNAVAILABLE")
    if persist_text is None and problem == "ABSENT":
        return _source("UNAVAILABLE", "SESSION_LIST_UNAVAILABLE")
    if persist_text is None:
        try:
            persist_text = preflight.read_persist_list("agent")
        except Exception as exc:
            reason = getattr(exc, "reason", "")
            if "timed out" in reason:
                return _source("UNAVAILABLE", "SESSION_LIST_TIMEOUT")
            if "unavailable" in reason:
                return _source("UNAVAILABLE", "SESSION_LIST_UNAVAILABLE")
            return _source("UNAVAILABLE", "SESSION_LIST_FAILED")
        if len(persist_text.encode("utf-8")) > MAX_TEXT_BYTES:
            return _source("UNAVAILABLE", "SESSION_LIST_UNBOUNDED")
    try:
        count = preflight.parse_persist_list(persist_text)
    except Exception:
        return _source("UNAVAILABLE", "SESSION_LIST_UNPARSEABLE")
    return _source("CAPTURED", "OK", persistent_count=count)


def _pressure(memory: dict, swap: dict, psi_memory: dict, psi_io: dict, sessions: dict) -> list[str]:
    facts: list[str] = []
    if memory.get("state") == "CAPTURED" and int(memory["available_bytes"]) < LOW_MEM_AVAILABLE_BYTES:
        facts.append("LOW_MEM_AVAILABLE")
    if swap.get("state") == "CAPTURED" and int(swap["used_bytes"]) >= MATERIAL_SWAP_USED_BYTES:
        facts.append("MATERIAL_SWAP_USE")
    if psi_memory.get("state") == "CAPTURED" and float(psi_memory["some_avg10"]) >= ELEVATED_PSI_AVG10:
        facts.append("ELEVATED_MEMORY_PRESSURE")
    if psi_io.get("state") == "CAPTURED" and float(psi_io["some_avg10"]) >= ELEVATED_PSI_AVG10:
        facts.append("ELEVATED_IO_PRESSURE")
    if sessions.get("state") == "CAPTURED" and int(sessions["persistent_count"]) >= HIGH_SESSION_COUNT:
        facts.append("HIGH_PERSISTENT_SESSION_COUNT")
    return [fact for fact in PRESSURE_ORDER if fact in facts]


def _overall(repository: dict, git: dict, sources: list[dict]) -> str:
    if repository.get("state") != "CAPTURED" or git.get("reason") != "OK":
        return "PARTIAL"
    if any(source.get("state") != "CAPTURED" for source in sources):
        return "PARTIAL"
    return "COMPLETE"


def _retention_destination(git_dir: Path, incident_id: str, capture_id: str) -> Path:
    git_resolved = git_dir.resolve()
    destination_dir = (git_resolved / "engineering-system" / "incidents" / incident_id).resolve()
    try:
        destination_dir.relative_to(git_resolved)
    except ValueError as exc:
        raise EvidenceBlocked("RETENTION_PATH_ESCAPE") from exc
    if destination_dir.exists() and not destination_dir.is_dir():
        raise EvidenceBlocked("RETENTION_UNTRUSTED")
    if destination_dir.is_symlink():
        raise EvidenceBlocked("RETENTION_PATH_ESCAPE")
    return destination_dir / f"{capture_id}.json"


def _enforce_bounds(destination: Path, payload: bytes) -> None:
    if len(payload) > MAX_RECORD_BYTES:
        raise EvidenceBlocked("RECORD_TOO_LARGE")
    directory = destination.parent
    if not directory.exists():
        return
    existing = []
    for path in directory.iterdir():
        if path.name.endswith(".partial"):
            continue
        if path.is_symlink() or not path.is_file():
            raise EvidenceBlocked("RETENTION_UNTRUSTED")
        if path.suffix != ".json":
            continue
        existing.append(path)
    if len(existing) >= MAX_CAPTURES_PER_INCIDENT:
        raise EvidenceBlocked("RETENTION_COUNT_BOUND")
    total = sum(path.stat().st_size for path in existing)
    if total + len(payload) > MAX_INCIDENT_BYTES:
        raise EvidenceBlocked("RETENTION_SIZE_BOUND")
    if destination.exists() or destination.is_symlink():
        raise EvidenceBlocked("CAPTURE_ID_COLLISION")


def _write_record(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved = destination.resolve()
    try:
        resolved.relative_to(destination.parent.resolve())
    except ValueError as exc:
        raise EvidenceBlocked("RETENTION_PATH_ESCAPE") from exc
    if destination.exists() or destination.is_symlink():
        raise EvidenceBlocked("CAPTURE_ID_COLLISION")
    temporary = destination.parent / f".{destination.name}.partial"
    try:
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists() and not destination.exists():
            temporary.unlink()
        raise


def build_record(
    *,
    incident_id: str,
    capture_id: str,
    captured_at: str,
    repository: dict,
    git: dict,
    memory: dict,
    swap: dict,
    load: dict,
    psi_memory: dict,
    psi_io: dict,
    sessions: dict,
    platform: str,
    support: str,
) -> dict:
    sources = [memory, swap, load, psi_memory, psi_io, sessions]
    return {
        "schema_version": 1,
        "kind": "incident-evidence",
        "incident_id": incident_id,
        "capture_id": capture_id,
        "captured_at": captured_at,
        "repository": repository,
        "git": git,
        "host": {"platform": platform, "support": support},
        "memory": memory,
        "swap": swap,
        "load": load,
        "psi_memory": psi_memory,
        "psi_io": psi_io,
        "sessions": sessions,
        "pressure_facts": _pressure(memory, swap, psi_memory, psi_io, sessions),
        "overall": _overall(repository, git, sources),
        "authority": "NONE",
    }


def _optional_text(path: str | None, label: str) -> tuple[str | None, str | None]:
    if path is None:
        return None, None
    text, problem = _read_bounded(Path(path), MAX_TEXT_BYTES)
    if problem == "ABSENT":
        raise EvidenceBlocked(f"{label}_ABSENT")
    return text, problem


def capture(args: argparse.Namespace) -> tuple[str, int]:
    try:
        if not _valid_incident_id(args.incident_id):
            raise EvidenceBlocked("INCIDENT_ID_INVALID")
        captured_at = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if parse_timestamp(captured_at) is None:
            raise EvidenceBlocked("NOW_INVALID")
        if args.capture_id:
            capture_id = args.capture_id
            if not CAPTURE_ID_RE.fullmatch(capture_id):
                raise EvidenceBlocked("CAPTURE_ID_INVALID")
        else:
            stamp = captured_at.replace("-", "").replace(":", "")
            capture_id = f"CAP-{stamp}-{secrets.token_hex(4)}"
        repository, git, git_dir = _git_identity(Path(args.root))
        platform, support = _host_platform()
        supported = support == "SUPPORTED"
        if args.meminfo_file:
            memory_text, memory_problem = _optional_text(args.meminfo_file, "MEMINFO_FILE")
        elif supported:
            memory_text, memory_problem = _read_bounded(Path("/proc/meminfo"), MAX_TEXT_BYTES)
        else:
            memory_text, memory_problem = None, None
        memory, swap = _memory_and_swap(memory_text, memory_problem, supported or bool(args.meminfo_file))
        load = _load(*_optional_text(args.loadavg_file, "LOADAVG_FILE"))
        if args.psi_memory_file or supported:
            psi_memory_text, psi_memory_problem = (
                _optional_text(args.psi_memory_file, "PSI_MEMORY_FILE")
                if args.psi_memory_file
                else _read_bounded(Path("/proc/pressure/memory"), MAX_TEXT_BYTES)
            )
        else:
            psi_memory_text, psi_memory_problem = None, None
        if args.psi_io_file or supported:
            psi_io_text, psi_io_problem = (
                _optional_text(args.psi_io_file, "PSI_IO_FILE")
                if args.psi_io_file
                else _read_bounded(Path("/proc/pressure/io"), MAX_TEXT_BYTES)
            )
        else:
            psi_io_text, psi_io_problem = None, None
        psi_memory = _psi(
            psi_memory_text,
            psi_memory_problem,
            supported or bool(args.psi_memory_file),
            "PSI_MEMORY_ABSENT",
            "PSI_MEMORY_UNPARSEABLE",
            "PSI_MEMORY_UNBOUNDED",
        )
        psi_io = _psi(
            psi_io_text,
            psi_io_problem,
            supported or bool(args.psi_io_file),
            "PSI_IO_ABSENT",
            "PSI_IO_UNPARSEABLE",
            "PSI_IO_UNBOUNDED",
        )
        if args.persist_list_file:
            session_text, session_problem = _optional_text(args.persist_list_file, "PERSIST_LIST_FILE")
        else:
            session_text, session_problem = None, None
        sessions = _sessions(session_text, session_problem)
        record = build_record(
            incident_id=args.incident_id,
            capture_id=capture_id,
            captured_at=captured_at,
            repository=repository,
            git=git,
            memory=memory,
            swap=swap,
            load=load,
            psi_memory=psi_memory,
            psi_io=psi_io,
            sessions=sessions,
            platform=platform,
            support=support,
        )
        errors = validate_record(record)
        if errors:
            raise EvidenceBlocked("SCHEMA_INVALID")
        payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
        destination = _retention_destination(git_dir, args.incident_id, capture_id)
        _enforce_bounds(destination, payload)
        _write_record(destination, payload)
    except EvidenceBlocked as exc:
        return block_capture(exc.reason)
    evidence_ref = f"engineering-system/incidents/{args.incident_id}/{capture_id}.json"
    facts = record["pressure_facts"]
    return (
        emit(
            {
                "RESULT": record["overall"],
                "AUTHORITY": "NONE",
                "OVERALL": record["overall"],
                "CAPTURE_ID": capture_id,
                "EVIDENCE_REF": evidence_ref,
                "PRESSURE_FACTS": ",".join(facts) if facts else "NONE",
                "ROOT_CAUSE": "UNPROVEN",
            }
        ),
        0,
    )


def check_packet(args: argparse.Namespace) -> tuple[str, int]:
    path = Path(args.packet_file)
    try:
        data = path.read_bytes()
    except OSError:
        return fail_packet("PACKET_UNREADABLE")
    if len(data) > MAX_PACKET_BYTES:
        return fail_packet("PACKET_TOO_LARGE")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return fail_packet("PACKET_UNREADABLE")
    return check_packet_text(text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-packet", help="Validate an Incident Packet file")
    check.add_argument("--packet-file", required=True)
    capture_parser = commands.add_parser("capture", help="Capture bounded read-only core evidence")
    capture_parser.add_argument("--root", required=True)
    capture_parser.add_argument("--incident-id", required=True)
    capture_parser.add_argument("--meminfo-file")
    capture_parser.add_argument("--psi-memory-file")
    capture_parser.add_argument("--psi-io-file")
    capture_parser.add_argument("--persist-list-file")
    capture_parser.add_argument("--loadavg-file")
    capture_parser.add_argument("--now")
    capture_parser.add_argument("--capture-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "check-packet":
        report, code = check_packet(args)
    else:
        report, code = capture(args)
    sys.stdout.write(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
