#!/usr/bin/env python3
"""Bounded read-only runtime evidence for one incident.

Resolves a command only from canonical runtime/operations metadata, requires a
host-signed production.read dispatch for the exact request, and runs that
command once. Raw output stays in the Git-local incident boundary. The report
is sanitized metadata and grants no mitigation authority.

Command:
  collect   Capture one health/logs/metrics/traces evidence request

Exit status:
  0  a result was emitted
  3  the request was malformed
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _load_module(name: str, filename: str):
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} is unavailable")
    module = sys.modules.get(name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


_RUNTIME = _load_module("runtime_contract", "runtime-contract.py")
check_contract = _RUNTIME.check_contract
_INCIDENT = _load_module("incident_evidence", "incident-evidence.py")

TIMEOUT_SECONDS = 5
OUTPUT_LIMIT_BYTES = 4096
EVIDENCE_KINDS = frozenset({"health", "logs", "metrics", "traces"})
EXCLUDED_KINDS = frozenset(
    {
        "start",
        "cleanup",
        "smoke",
        "e2e",
        "browser",
        "backup",
        "restore",
        "rollback",
        "restart",
        "deploy",
        "release",
    }
)
KIND_FIELDS = {
    "health": "operations.health_command",
    "logs": "logs",
    "metrics": "metrics",
    "traces": "traces",
}
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
INCIDENT_ID_RE = re.compile(r"^INC-[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*$")
CAPTURE_ID_RE = re.compile(r"^CAP-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
SENSITIVE_RE = re.compile(
    r"(BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,})",
)
FORBIDDEN_KEYS = frozenset(
    {
        "command",
        "commands",
        "shell",
        "argv",
        "execute",
        "exec",
        "script",
        "endpoint",
        "signing_key",
        "private_key",
        "mint",
    }
)
REQUEST_KEYS = frozenset(
    {
        "incident_id",
        "target_repo",
        "subject_head",
        "evidence_kind",
        "canonical_field_or_capability",
        "command_sha256",
        "retention_subject",
        "verification",
    }
)


def _load_skills_contract():
    return _load_module("skills_contract", "skills-contract.py")


_SKILLS = _load_skills_contract()
CANONICAL_ROOT = Path(__file__).resolve().parents[1]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _forbidden_key(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip().lower() in FORBIDDEN_KEYS:
                return str(key)
            found = _forbidden_key(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _forbidden_key(item)
            if found:
                return found
    return None


def _git(root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "--no-replace-objects", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _normalized_repo(url: str) -> str | None:
    text = url.strip()
    if text.endswith(".git"):
        text = text[:-4]
    marker = "github.com/"
    if marker in text:
        tail = text.split(marker, 1)[1]
    elif "github.com:" in text:
        tail = text.split("github.com:", 1)[1]
    else:
        return None
    parts = [part for part in tail.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _result(
    result: str,
    reason: str,
    *,
    kind: str = "",
    capture_id: str = "",
    evidence_ref: str = "",
    content_digest: str = "",
    executed: bool = False,
) -> dict[str, str]:
    return {
        "RESULT": result,
        "REASON": reason,
        "EVIDENCE_KIND": kind,
        "CAPTURE_ID": capture_id,
        "EVIDENCE_REF": evidence_ref,
        "CONTENT_DIGEST": content_digest,
        "EXECUTED": "YES" if executed else "NO",
        "MITIGATION_AUTHORITY": "NONE",
    }


def _format(report: dict[str, str]) -> str:
    keys = (
        "RESULT",
        "REASON",
        "EVIDENCE_KIND",
        "CAPTURE_ID",
        "EVIDENCE_REF",
        "CONTENT_DIGEST",
        "EXECUTED",
        "MITIGATION_AUTHORITY",
    )
    return "\n".join(f"{key}={report[key]}" for key in keys) + "\n"


def _git_blob(root: Path, rev: str, path: str) -> bytes | None:
    completed = _git_readonly(root, "show", f"{rev}:{path}")
    if completed.returncode != 0:
        return None
    return completed.stdout


def _committed_contract_root(root: Path, subject_head: str) -> tempfile.TemporaryDirectory[str]:
    temporary = tempfile.TemporaryDirectory(prefix="runtime-evidence-")
    base = Path(temporary.name)
    for relative in (
        ".engineering/project.yaml",
        ".engineering/runtime.yaml",
        ".engineering/release.yaml",
    ):
        blob = _git_blob(root, subject_head, relative)
        if blob is None:
            continue
        destination = base / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob)
    schema = base / "schemas" / "runtime-contract.schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_bytes((CANONICAL_ROOT / "schemas" / "runtime-contract.schema.json").read_bytes())
    return temporary


def _resolve_command(root: Path, subject_head: str, kind: str) -> tuple[str, str]:
    temporary = _committed_contract_root(root, subject_head)
    try:
        report = check_contract(Path(temporary.name))
    finally:
        temporary.cleanup()
    if report["result"] != "PASS":
        return "", "CONTRACT_INVALID"
    if kind == "health":
        health = report["authorities"]["health"]
        if health["support"] != "SUPPORTED" or not health["command"]:
            return "", "UNSUPPORTED"
        return health["command"], ""
    capability = report["capabilities"].get(kind) or {}
    if capability.get("support") != "SUPPORTED" or not capability.get("command"):
        return "", "UNSUPPORTED"
    return str(capability["command"]), ""


def _retention_ref(incident_id: str, capture_id: str) -> str:
    return f"engineering-system/incidents/{incident_id}/{capture_id}.json"


def _parse_capture(incident_id: str, retention_subject: str) -> str | None:
    prefix = f"engineering-system/incidents/{incident_id}/"
    if not retention_subject.startswith(prefix) or not retention_subject.endswith(".json"):
        return None
    capture_id = retention_subject[len(prefix) : -len(".json")]
    if not CAPTURE_ID_RE.fullmatch(capture_id):
        return None
    if _retention_ref(incident_id, capture_id) != retention_subject:
        return None
    return capture_id


def _authorize(root: Path, request: dict[str, Any], effect: dict[str, Any]) -> tuple[str, str]:
    verification = request.get("verification")
    if not isinstance(verification, dict):
        return "BOUNDARY_UNAVAILABLE", "TRUST_BOUNDARY_UNAVAILABLE"
    anchor = _SKILLS.resolve_trust_anchor()
    if anchor is None:
        return "BOUNDARY_UNAVAILABLE", "TRUST_BOUNDARY_UNAVAILABLE"
    try:
        binding = Path(str(verification["binding_assertion"]))
        dispatch = Path(str(verification["dispatch_assertion"]))
        payload = _SKILLS.verify_signed_json(dispatch, anchor)
    except (KeyError, OSError, json.JSONDecodeError, SystemExit):
        return "BOUNDARY_UNAVAILABLE", "TRUST_BOUNDARY_UNAVAILABLE"
    classes = payload.get("classes")
    if payload.get("tool_id") != "production.read" or not isinstance(classes, list) or "production_read" not in classes:
        return "AUTHORIZATION_DENIED", "DISPATCH_CLASS"
    if payload.get("request_sha256") != _SKILLS.canonical_request_sha256(effect):
        return "AUTHORIZATION_DENIED", "REQUEST_BINDING_MISMATCH"
    try:
        decision = _SKILLS.authorize(
            CANONICAL_ROOT,
            binding_assertion=binding,
            dispatch_assertion=dispatch,
            request_json=json.dumps(effect),
        )
    except SystemExit:
        return "BOUNDARY_UNAVAILABLE", "TRUST_BOUNDARY_UNAVAILABLE"
    if decision.allowed:
        return "ALLOW", "ALLOW"
    if decision.reason in {"BOUNDARY_UNAVAILABLE", "ASSERTION_MISSING", "SIGNATURE_MISMATCH", "TRUST_ANCHOR_MISMATCH"}:
        return "BOUNDARY_UNAVAILABLE", "TRUST_BOUNDARY_UNAVAILABLE"
    return "AUTHORIZATION_DENIED", decision.reason


def _stop_group(proc: subprocess.Popen[bytes], stdout: object) -> None:
    try:
        getattr(os, "killpg")(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.wait(timeout=2)
    close = getattr(stdout, "close", None)
    if close is not None:
        try:
            close()
        except OSError:
            pass


TRUSTED_BIN_DIRS = (Path("/usr/bin"), Path("/bin"))
TRUSTED_ROOTS = (Path("/usr"), Path("/bin"))


def _trusted_argv(argv: list[str]) -> list[str] | None:
    if not argv:
        return None
    program = argv[0]
    if not program or program.startswith("-") or (program.startswith(".") or "/" in program[1:] and not program.startswith("/")):
        return None
    candidate: Path | None
    if program.startswith("/"):
        candidate = Path(program)
    else:
        candidate = None
        for directory in TRUSTED_BIN_DIRS:
            probe = directory / program
            if probe.exists():
                candidate = probe
                break
        if candidate is None:
            return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if not any(resolved == root or root in resolved.parents for root in TRUSTED_ROOTS):
        return None
    rest = list(argv[1:])
    if resolved.name == "python" or resolved.name.startswith("python"):
        if "-I" not in rest:
            if "-E" not in rest:
                rest.insert(0, "-E")
            if "-s" not in rest:
                rest.insert(1 if rest and rest[0] == "-E" else 0, "-s")
    return [str(resolved), *rest]


def _execution_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


LAUNCHERS = frozenset(
    {
        "env",
        "nice",
        "nohup",
        "timeout",
        "stdbuf",
        "xargs",
        "sudo",
        "doas",
        "ionice",
        "taskset",
        "watch",
        "flock",
        "setsid",
        "chrt",
        "numactl",
        "time",
        "busybox",
    }
)
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "fish"})


def _subject_code_path(raw: str, work: Path) -> bool:
    if not raw or raw.startswith(("/", "~")) or "\x00" in raw:
        return False
    path = Path(raw)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return False
    base = work.resolve()
    try:
        candidate = (work / path).resolve()
    except OSError:
        return False
    if candidate != base and base not in candidate.parents:
        return False
    return candidate.is_file() and not candidate.is_symlink()


def _python_code_bound(rest: list[str], work: Path) -> bool:
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--":
            if index + 1 >= len(rest):
                return False
            return _subject_code_path(rest[index + 1], work)
        if arg == "-c":
            return index + 1 < len(rest)
        if arg == "-m" or (arg.startswith("-m") and arg != "-m"):
            return False
        if arg in {"-W", "-X"}:
            index += 2
            continue
        if arg.startswith("-W") or arg.startswith("-X"):
            index += 1
            continue
        if arg.startswith("-") and arg != "-":
            index += 1
            continue
        return _subject_code_path(arg, work)
    return False


def _shell_code_bound(rest: list[str], work: Path) -> bool:
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in {"-c", "--command"}:
            return index + 1 < len(rest)
        if arg.startswith("-") and not arg.startswith("--") and arg != "-":
            if "c" in arg[1:]:
                return index + 1 < len(rest)
            index += 1
            continue
        if arg.startswith("--"):
            index += 1
            continue
        return _subject_code_path(arg, work)
    return False


def _inline_or_subject_script(rest: list[str], work: Path, inline_flags: set[str]) -> bool:
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in inline_flags:
            return index + 1 < len(rest)
        if arg.startswith("-") and arg != "-":
            index += 1
            continue
        return _subject_code_path(arg, work)
    return False


def _executable_arguments_bound(argv: list[str], work: Path) -> bool:
    name = Path(argv[0]).name
    if name in LAUNCHERS:
        return False
    rest = argv[1:]
    if name == "python" or name.startswith("python"):
        return _python_code_bound(rest, work)
    if name in SHELLS:
        return _shell_code_bound(rest, work)
    if name in {"perl", "ruby", "node", "php", "lua"}:
        return _inline_or_subject_script(rest, work, {"-e", "-c"})
    return True


def _git_readonly(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "--no-replace-objects",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=",
            *args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=text,
        env=env,
    )


def _materialize_subject_tree(root: Path, subject_head: str) -> tuple[tempfile.TemporaryDirectory[str], Path] | None:
    """Copy committed blobs into a private directory.

    Uses read-only object lookup and ignores repository replace refs. Does not
    register a worktree, checkout, or run repository hooks, filters, or other
    local configuration.
    """
    listed = _git_readonly(root, "ls-tree", "-r", "-z", "--full-tree", subject_head)
    if listed.returncode != 0:
        return None
    temporary = tempfile.TemporaryDirectory(prefix="runtime-evidence-exec-")
    work = Path(temporary.name) / "tree"
    try:
        work.mkdir()
        base = work.resolve()
        for entry in listed.stdout.split(b"\0"):
            if not entry:
                continue
            meta, separator, path = entry.partition(b"\t")
            if separator != b"\t":
                raise ValueError("tree entry")
            mode, kind, oid = meta.split()
            if kind != b"blob" or mode in {b"120000", b"160000"}:
                raise ValueError("unsupported tree entry")
            relative = path.decode("utf-8", "surrogateescape")
            if relative.startswith("/") or "\x00" in relative:
                raise ValueError("tree path")
            destination = (work / relative).resolve()
            if destination != base and base not in destination.parents:
                raise ValueError("tree path")
            blob = _git_readonly(root, "cat-file", "blob", oid.decode("ascii"))
            if blob.returncode != 0:
                raise ValueError("blob")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blob.stdout)
            destination.chmod(0o755 if mode == b"100755" else 0o644)
    except (OSError, UnicodeError, ValueError):
        temporary.cleanup()
        return None
    return temporary, work


def _release_subject_tree(temporary: tempfile.TemporaryDirectory[str]) -> None:
    temporary.cleanup()


def _execute_once(root: Path, subject_head: str, command: str) -> tuple[str, str, bytes]:
    try:
        argv = shlex.split(command)
    except ValueError:
        return "EXECUTION_FAILED", "COMMAND_PARSE", b""
    if not argv:
        return "EXECUTION_FAILED", "COMMAND_EMPTY", b""
    trusted = _trusted_argv(argv)
    if trusted is None:
        return "EXECUTION_FAILED", "EXECUTABLE_UNTRUSTED", b""
    checked_out = _materialize_subject_tree(root, subject_head)
    if checked_out is None:
        return "EXECUTION_FAILED", "SUBJECT_TREE", b""
    temporary, work = checked_out
    if not _executable_arguments_bound(trusted, work):
        _release_subject_tree(temporary)
        return "EXECUTION_FAILED", "CODE_UNBOUND", b""
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            trusted,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=True,
            env=_execution_environment(),
        )
        stdout = proc.stdout
        if stdout is None:
            _stop_group(proc, stdout)
            return "EXECUTION_FAILED", "OUTPUT_PIPE", b""
        fd = stdout.fileno()
        os.set_blocking(fd, False)
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + TIMEOUT_SECONDS

        def absorb(block: bytes) -> bool:
            nonlocal total
            if total + len(block) > OUTPUT_LIMIT_BYTES:
                return False
            chunks.append(block)
            total += len(block)
            return True

        status = ""
        while not status:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                break
            readable, _, _ = select.select([fd], [], [], min(0.2, remaining))
            if not readable:
                if proc.poll() is not None:
                    status = "exited"
                continue
            try:
                block = os.read(fd, 1024)
            except BlockingIOError:
                continue
            if not block:
                status = "exited"
                break
            if not absorb(block):
                status = "limit"
                break
        if status == "exited":
            while True:
                try:
                    block = os.read(fd, 1024)
                except BlockingIOError:
                    break
                if not block:
                    break
                if not absorb(block):
                    status = "limit"
                    break
        if status in {"limit", "timeout"}:
            if status == "limit":
                return "OUTPUT_UNBOUNDED", "OUTPUT_LIMIT", b""
            return "TIMEOUT", "TIMEOUT", b""
        code = proc.wait(timeout=2)
        stdout.close()
        output = b"".join(chunks)
        if code != 0:
            return "EXECUTION_FAILED", f"EXIT_{code}", output
        return "CAPTURED", "OK", output
    finally:
        if proc is not None:
            _stop_group(proc, proc.stdout)
        _release_subject_tree(temporary)


def _retention_destination(root: Path, incident_id: str, capture_id: str) -> Path:
    git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    if not git_dir:
        raise _INCIDENT.EvidenceBlocked("GIT_DIR_UNAVAILABLE")
    return _INCIDENT._retention_destination(Path(git_dir), incident_id, capture_id)


def _stored_result(record: dict[str, Any], kind: str, capture_id: str) -> dict[str, str] | None:
    state = str(record.get("result") or "")
    if state in {"", "RESERVED"}:
        return None
    evidence_ref = ""
    if state == "CAPTURED":
        incident_id = str(record.get("incident_id") or "")
        if incident_id:
            evidence_ref = _retention_ref(incident_id, capture_id)
    return _result(
        state,
        "ALREADY_CAPTURED" if state == "CAPTURED" else str(record.get("reason") or state),
        kind=str(record.get("evidence_kind") or kind),
        capture_id=capture_id,
        evidence_ref=evidence_ref,
        content_digest=str(record.get("content_digest") or ""),
        executed=False,
    )


def _await_capture(destination: Path, kind: str, capture_id: str) -> dict[str, str]:
    deadline = time.monotonic() + 5
    while True:
        if destination.is_symlink() or not destination.is_file():
            return _result("UNAVAILABLE", "RETENTION_UNTRUSTED", kind=kind, capture_id=capture_id)
        try:
            record = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {"result": "RESERVED"}
        if not isinstance(record, dict):
            record = {"result": "RESERVED"}
        stored = _stored_result(record, kind, capture_id)
        if stored is not None:
            return stored
        if time.monotonic() >= deadline:
            return _result("UNAVAILABLE", "RESERVATION_PENDING", kind=kind, capture_id=capture_id)
        time.sleep(0.02)


def _claim_capture(root: Path, incident_id: str, capture_id: str, kind: str) -> dict[str, str] | Path:
    destination = _retention_destination(root, incident_id, capture_id)
    if destination.is_symlink():
        raise _INCIDENT.EvidenceBlocked("RETENTION_PATH_ESCAPE")
    if destination.exists():
        return _await_capture(destination, kind, capture_id)
    _INCIDENT._enforce_bounds(destination, b"{}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schema_version": 1,
            "kind": "runtime-evidence",
            "incident_id": incident_id,
            "capture_id": capture_id,
            "evidence_kind": kind,
            "result": "RESERVED",
        },
        sort_keys=True,
    ).encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _await_capture(destination, kind, capture_id)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return destination


def _finalize_capture(destination: Path, record: dict[str, Any]) -> str:
    if destination.is_symlink() or not destination.is_file():
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    payload = json.dumps(record, sort_keys=True).encode("utf-8")
    if len(payload) > _INCIDENT.MAX_RECORD_BYTES:
        raise _INCIDENT.EvidenceBlocked("RECORD_TOO_LARGE")
    temporary = destination.with_name(f".{destination.name}.partial")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
    incident_id = str(record.get("incident_id") or "")
    capture_id = str(record.get("capture_id") or "")
    return _retention_ref(incident_id, capture_id)


def collect(root: Path, request: dict[str, Any]) -> dict[str, str]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    forbidden = _forbidden_key(request)
    if forbidden:
        return _result("AUTHORIZATION_DENIED", f"REQUEST_COMMAND_REJECTED:{forbidden}")
    unknown = sorted(set(request) - REQUEST_KEYS)
    if unknown:
        raise ValueError(f"unknown request key {unknown[0]}")
    kind = str(request.get("evidence_kind") or "")
    if kind in EXCLUDED_KINDS or kind not in EVIDENCE_KINDS:
        return _result("AUTHORIZATION_DENIED", "EXCLUDED_KIND", kind=kind)
    incident_id = str(request.get("incident_id") or "")
    if not INCIDENT_ID_RE.fullmatch(incident_id):
        raise ValueError("incident_id is invalid")
    subject_head = str(request.get("subject_head") or "")
    if not FULL_SHA_RE.fullmatch(subject_head):
        raise ValueError("subject_head is invalid")
    target_repo = str(request.get("target_repo") or "")
    field = str(request.get("canonical_field_or_capability") or "")
    command_sha = str(request.get("command_sha256") or "")
    retention_subject = str(request.get("retention_subject") or "")
    capture_id = _parse_capture(incident_id, retention_subject)
    if capture_id is None:
        return _result("AUTHORIZATION_DENIED", "RETENTION_SUBJECT", kind=kind)
    if field != KIND_FIELDS[kind]:
        return _result("AUTHORIZATION_DENIED", "CANONICAL_FIELD", kind=kind, capture_id=capture_id)

    head = _git(root, "rev-parse", "HEAD")
    if head != subject_head:
        return _result("STALE_HEAD", "HEAD_MISMATCH", kind=kind, capture_id=capture_id)
    origin = _git(root, "remote", "get-url", "origin")
    if _normalized_repo(origin or "") != target_repo:
        return _result("AUTHORIZATION_DENIED", "REPO_MISMATCH", kind=kind, capture_id=capture_id)

    command, problem = _resolve_command(root, subject_head, kind)
    if problem == "CONTRACT_INVALID":
        return _result("UNAVAILABLE", "CONTRACT_INVALID", kind=kind, capture_id=capture_id)
    if problem or not command:
        return _result("UNSUPPORTED", "UNSUPPORTED", kind=kind, capture_id=capture_id)
    if command_sha != _sha256_text(command):
        return _result("AUTHORIZATION_DENIED", "COMMAND_HASH_MISMATCH", kind=kind, capture_id=capture_id)

    effect = {
        "canonical_field_or_capability": field,
        "command_sha256": command_sha,
        "evidence_kind": kind,
        "incident_id": incident_id,
        "retention_subject": retention_subject,
        "subject_head": subject_head,
        "target_repo": target_repo,
    }
    gate, reason = _authorize(root, request, effect)
    if gate != "ALLOW":
        return _result(gate, reason, kind=kind, capture_id=capture_id)

    try:
        claim = _claim_capture(root, incident_id, capture_id, kind)
    except _INCIDENT.EvidenceBlocked as exc:
        if exc.reason == "CAPTURE_ID_COLLISION":
            try:
                return _await_capture(_retention_destination(root, incident_id, capture_id), kind, capture_id)
            except _INCIDENT.EvidenceBlocked as nested:
                return _result("UNAVAILABLE", nested.reason, kind=kind, capture_id=capture_id)
        return _result("UNAVAILABLE", exc.reason, kind=kind, capture_id=capture_id)
    if isinstance(claim, dict):
        return claim

    status, exec_reason, output = _execute_once(root, subject_head, command)
    digest = hashlib.sha256(output).hexdigest() if output else ""
    executed = exec_reason not in {
        "COMMAND_PARSE",
        "COMMAND_EMPTY",
        "SUBJECT_TREE",
        "EXECUTABLE_UNTRUSTED",
        "CODE_UNBOUND",
    }
    terminal = {
        "schema_version": 1,
        "kind": "runtime-evidence",
        "incident_id": incident_id,
        "capture_id": capture_id,
        "evidence_kind": kind,
        "subject_head": subject_head,
        "result": status,
        "reason": exec_reason,
        "content_digest": digest,
        "mitigation_authority": "NONE",
    }
    if status == "CAPTURED" and not SENSITIVE_RE.search(output.decode("utf-8", errors="replace")):
        terminal["raw_output"] = output.decode("utf-8", errors="replace")
    try:
        evidence_ref = _finalize_capture(claim, terminal)
    except _INCIDENT.EvidenceBlocked as exc:
        return _result(
            "UNAVAILABLE",
            exc.reason,
            kind=kind,
            capture_id=capture_id,
            content_digest=digest,
            executed=executed,
        )
    if status != "CAPTURED":
        return _result(status, exec_reason, kind=kind, capture_id=capture_id, content_digest=digest, executed=executed)
    if "raw_output" not in terminal:
        return _result(
            "BLOCKED_SENSITIVE_OUTPUT",
            "SENSITIVE_OUTPUT",
            kind=kind,
            capture_id=capture_id,
            content_digest=digest,
            executed=True,
        )
    return _result(
        "CAPTURED",
        "OK",
        kind=kind,
        capture_id=capture_id,
        evidence_ref=evidence_ref,
        content_digest=digest,
        executed=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect",))
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--root", default=".")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
        report = collect(Path(args.root).resolve(), payload)
        sys.stdout.write(_format(report))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        sys.stdout.write(_format(_result("UNAVAILABLE", str(exc).splitlines()[0][:120])))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
