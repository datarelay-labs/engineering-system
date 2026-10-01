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
# Pre-execution subject checkout stays inside a fixed ceiling. The reproduced
# unbounded case copied 2500 one-kibibyte blobs (2_560_000 bytes, about 15s)
# before the command deadline started.
MAX_SUBJECT_FILES = 128
MAX_SUBJECT_BYTES = 1_048_576
MAX_SUBJECT_SECONDS = 2
TRACE_LIMIT_BYTES = 262_144
TRACE_QUOTED_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
TRACE_DIRFD_PATH_RE = re.compile(
    r'(?:\(|,\s*)(AT_FDCWD|-?\d+)(?:<[^>]*>)?,\s*"((?:\\.|[^"\\])*)"'
)
TRACE_ROOT_CHANGE_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?(?:chroot|pivot_root)\("
)
TRACE_MOUNT_TOPOLOGY_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?(?:mount|umount2|move_mount|open_tree|mount_setattr)\("
)
TRACE_PATH_TOPOLOGY_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?(?:rename|renameat|renameat2|link|linkat|symlink|symlinkat)\("
)
TRACE_IO_URING_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?io_uring_(?:setup|enter|register)\("
)
TRACE_SECCOMP_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?seccomp\("
)
TRACE_PRCTL_RE = re.compile(
    r"^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?prctl\("
)
TRACE_SYSCALL_FILTER = (
    "trace=%file,%process,io_uring_setup,io_uring_enter,io_uring_register,seccomp,prctl"
)
TRACE_SETUP_PRIVATE_MOUNT_RE = re.compile(
    r'^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?mount\("none", "/", NULL, MS_REC\|MS_PRIVATE, NULL\) = 0$'
)
TRACE_SETUP_PROC_MOUNT_RE = re.compile(
    r'^\s*(?:(?:\[pid\s+\d+\]|\d+)\s+)?mount\("proc", "/proc", "proc", MS_NOSUID\|MS_NODEV\|MS_NOEXEC, NULL\) = 0$'
)
TRACE_DEV_FD_ALIAS_RE = re.compile(
    r"^/dev/(?:fd(?:/|$)|stdin(?:/|$)|stdout(?:/|$)|stderr(?:/|$))"
)
TRACE_TARGET_LAUNCHER = """import os, resource, sys
limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
if limit == resource.RLIM_INFINITY or limit > 1048576:
    limit = 1048576
os.closerange(3, int(limit))
os.execv(sys.argv[1], sys.argv[1:])
"""
IDENTITY_FIELDS = (
    "incident_id",
    "evidence_kind",
    "subject_head",
    "canonical_field_or_capability",
    "command_sha256",
    "target_repo",
    "retention_subject",
)
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


PYTHON_SAFE_FLAGS = frozenset({"-E", "-s", "-S", "-B", "-b", "-q", "-u", "-v", "-O", "-OO", "-I"})


def _python_code_bound(rest: list[str], work: Path) -> bool:
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--":
            if index + 1 >= len(rest):
                return False
            return _subject_code_path(rest[index + 1], work)
        if arg == "-c":
            return index + 1 < len(rest) and bool(rest[index + 1])
        if arg == "-m" or arg.startswith("-m"):
            return False
        if arg in {"-W", "-X"}:
            if index + 1 >= len(rest):
                return False
            index += 2
            continue
        if (arg.startswith("-W") or arg.startswith("-X")) and len(arg) > 2:
            index += 1
            continue
        if arg in PYTHON_SAFE_FLAGS:
            index += 1
            continue
        if arg.startswith("-"):
            return False
        return _subject_code_path(arg, work)
    return False


def _shell_code_bound(rest: list[str], work: Path) -> bool:
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in {"-c", "--command"}:
            return index + 1 < len(rest) and bool(rest[index + 1])
        if arg == "--":
            if index + 1 >= len(rest):
                return False
            return _subject_code_path(rest[index + 1], work)
        if arg.startswith("-"):
            return False
        return _subject_code_path(arg, work)
    return False


AWK_PROGRAMS = frozenset({"awk", "gawk", "nawk", "mawk"})
CURL_FLAGS = frozenset({"--fail", "--silent", "--show-error", "--head", "--location", "--http1.1", "--http1.0", "--http2"})
CURL_VALUE_FLAGS = frozenset({"-m", "--max-time", "--connect-timeout", "--retry"})


def _awk_code_bound(rest: list[str], work: Path) -> bool:
    saw_program = False
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--":
            index += 1
            break
        if arg == "-i" or arg.startswith("--include"):
            return False
        if arg in {"-f", "-E"}:
            if index + 1 >= len(rest) or not _subject_code_path(rest[index + 1], work):
                return False
            saw_program = True
            index += 2
            continue
        if arg.startswith("--file="):
            if not _subject_code_path(arg.split("=", 1)[1], work):
                return False
            saw_program = True
            index += 1
            continue
        if arg == "-e":
            if index + 1 >= len(rest) or not rest[index + 1]:
                return False
            saw_program = True
            index += 2
            continue
        if arg.startswith("--source="):
            if not arg.split("=", 1)[1]:
                return False
            saw_program = True
            index += 1
            continue
        if arg in {"-F", "-v"}:
            if index + 1 >= len(rest):
                return False
            index += 2
            continue
        if arg.startswith("-F") and len(arg) > 2:
            index += 1
            continue
        if arg.startswith("-"):
            return False
        if not saw_program:
            if not arg:
                return False
            saw_program = True
            index += 1
            continue
        index += 1
    while index < len(rest):
        if not rest[index]:
            return False
        index += 1
    return saw_program


def _curl_data_bound(rest: list[str]) -> bool:
    saw_target = False
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--":
            index += 1
            break
        if arg in CURL_FLAGS:
            index += 1
            continue
        if arg in CURL_VALUE_FLAGS:
            if index + 1 >= len(rest) or not rest[index + 1].isdigit():
                return False
            index += 2
            continue
        if arg.startswith("-") and not arg.startswith("--"):
            if len(arg) == 1 or any(char not in "fsSIL" for char in arg[1:]):
                return False
            index += 1
            continue
        if arg.startswith("-"):
            return False
        if not arg or "\x00" in arg:
            return False
        saw_target = True
        index += 1
    while index < len(rest):
        if not rest[index] or "\x00" in rest[index]:
            return False
        saw_target = True
        index += 1
    return saw_target


def _executable_arguments_bound(argv: list[str], work: Path) -> bool:
    """Allow only recognized command shapes.

    Unknown tools fail closed. A trusted binary path does not make its
    arguments safe to execute.
    """
    name = Path(argv[0]).name
    if name in LAUNCHERS:
        return False
    rest = argv[1:]
    if name == "python" or name.startswith("python"):
        return _python_code_bound(rest, work)
    if name in SHELLS:
        return _shell_code_bound(rest, work)
    if name in AWK_PROGRAMS:
        return _awk_code_bound(rest, work)
    if name == "curl":
        return _curl_data_bound(rest)
    return False


def _git_readonly(
    root: Path,
    *args: str,
    text: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    command = [
        "git",
        "-C",
        str(root),
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=",
        *args,
    ]
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=text,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "" if text else b"", "" if text else b"")


def _materialize_subject_tree(
    root: Path,
    subject_head: str,
) -> tuple[tempfile.TemporaryDirectory[str], Path, tuple[str, ...]] | None:
    """Copy exact-HEAD regular blobs into a private directory.

    Committed symlinks and gitlinks are represented only by inert placeholders:
    a dangling symlink and an empty directory respectively. Their paths are
    returned so execution can prove they were not observed by the command.
    The materializer never follows a symlink or fetches submodule content.
    """
    deadline = time.monotonic() + MAX_SUBJECT_SECONDS
    if time.monotonic() >= deadline:
        return None
    listed = _git_readonly(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        subject_head,
        timeout=deadline - time.monotonic(),
    )
    if listed.returncode != 0 or not isinstance(listed.stdout, bytes):
        return None
    entries = [entry for entry in listed.stdout.split(b"\0") if entry]
    if len(entries) > MAX_SUBJECT_FILES:
        return None
    temporary = tempfile.TemporaryDirectory(prefix="runtime-evidence-exec-")
    work = Path(temporary.name) / "tree"
    total = 0
    unsafe_paths: list[str] = []
    try:
        work.mkdir()
        base = work.resolve()
        for entry in entries:
            if time.monotonic() >= deadline:
                raise ValueError("tree deadline")
            meta, separator, path = entry.partition(b"\t")
            if separator != b"\t":
                raise ValueError("tree entry")
            mode, kind, oid = meta.split()
            relative = path.decode("utf-8", "surrogateescape")
            if relative.startswith("/") or "\x00" in relative:
                raise ValueError("tree path")
            destination = (work / relative).resolve()
            if destination != base and base not in destination.parents:
                raise ValueError("tree path")
            if mode in {b"120000", b"160000"}:
                destination.parent.mkdir(parents=True, exist_ok=True)
                unsafe_paths.append(relative)
                if mode == b"120000":
                    marker = hashlib.sha256(relative.encode("utf-8", "surrogateescape")).hexdigest()[:16]
                    destination.symlink_to(f"/__engineering_runtime_evidence_blocked__/{marker}")
                else:
                    destination.mkdir()
                continue
            if kind != b"blob":
                raise ValueError("unsupported tree entry")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("tree deadline")
            blob = _git_readonly(root, "cat-file", "blob", oid.decode("ascii"), timeout=remaining)
            if blob.returncode != 0 or not isinstance(blob.stdout, bytes):
                raise ValueError("blob")
            size = len(blob.stdout)
            if size > MAX_SUBJECT_BYTES or total + size > MAX_SUBJECT_BYTES:
                raise ValueError("tree bytes")
            total += size
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blob.stdout)
            destination.chmod(0o755 if mode == b"100755" else 0o644)
    except (OSError, UnicodeError, ValueError):
        temporary.cleanup()
        return None
    return temporary, work, tuple(unsafe_paths)


def _release_subject_tree(temporary: tempfile.TemporaryDirectory[str]) -> None:
    temporary.cleanup()


def _trusted_trace_argv() -> list[str] | None:
    return _trusted_argv(["strace"])


def _trace_isolation_supported() -> bool:
    unshare = _trusted_argv(["unshare"])
    probe = _trusted_argv(["true"])
    if unshare is None or probe is None:
        return False
    try:
        completed = subprocess.run(
            [*unshare, "-c", "-p", "-f", "--mount-proc", "--", *probe],
            cwd="/",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=_execution_environment(),
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _decode_trace_string(raw: str) -> str | None:
    decoded = bytearray()
    index = 0
    simple = {
        "a": 7,
        "b": 8,
        "t": 9,
        "n": 10,
        "v": 11,
        "f": 12,
        "r": 13,
        "\\": 92,
        '"': 34,
    }
    while index < len(raw):
        char = raw[index]
        if char != "\\":
            try:
                decoded.extend(os.fsencode(char))
            except UnicodeEncodeError:
                return None
            index += 1
            continue
        index += 1
        if index >= len(raw):
            return None
        escape = raw[index]
        if escape in "01234567":
            end = index + 1
            while end < len(raw) and end < index + 3 and raw[end] in "01234567":
                end += 1
            decoded.append(int(raw[index:end], 8))
            index = end
            continue
        if escape == "x":
            if index + 2 >= len(raw):
                return None
            token = raw[index + 1 : index + 3]
            if not all(item in "0123456789abcdefABCDEF" for item in token):
                return None
            decoded.append(int(token, 16))
            index += 3
            continue
        value = simple.get(escape)
        if value is None:
            return None
        decoded.append(value)
        index += 1
    return os.fsdecode(bytes(decoded))


def _normalize_trace_path(path: str) -> str:
    normalized = os.path.normpath(path)
    # Linux resolves exactly two leading slashes as the ordinary filesystem
    # root even though posixpath.normpath intentionally preserves them.
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    return normalized


def _trace_path_alias_ambiguous(path: str) -> bool:
    if not os.path.isabs(path):
        return False
    normalized = _normalize_trace_path(path)
    if TRACE_DEV_FD_ALIAS_RE.match(normalized):
        return True
    if not normalized.startswith("/proc/"):
        return False
    parts = [part for part in normalized.split("/") if part]
    if not parts or parts[0] != "proc":
        return False
    return any(part in {"cwd", "root", "fd"} for part in parts[1:])


def _trace_setup_mount_allowed(line: str) -> bool:
    # util-linux unshare --mount-proc performs exactly these two mounts before
    # the evidence command starts. Match the whole syscall including flags so
    # a tracee cannot disguise a bind/move mount with the same source/target.
    return bool(
        TRACE_SETUP_PRIVATE_MOUNT_RE.fullmatch(line)
        or TRACE_SETUP_PROC_MOUNT_RE.fullmatch(line)
    )


def _trace_unsafe_access_reason(trace_source: str | Path, work: Path, unsafe_paths: tuple[str, ...]) -> str | None:
    if isinstance(trace_source, Path):
        try:
            if not trace_source.is_file():
                return "TRACE_UNAVAILABLE"
            if trace_source.stat().st_size > TRACE_LIMIT_BYTES:
                return "TRACE_LIMIT"
            trace = trace_source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "TRACE_UNAVAILABLE"
    else:
        trace = trace_source
        if len(trace.encode("utf-8", errors="replace")) > TRACE_LIMIT_BYTES:
            return "TRACE_LIMIT"

    base = _normalize_trace_path(str(work.resolve()))
    unsafe = tuple(Path(item).as_posix().rstrip("/") for item in unsafe_paths)
    for line in trace.splitlines():
        # Filesystem-root changes invalidate absolute-path attribution. Runtime
        # evidence never needs to alter the tracee root, so even an attempted
        # chroot/pivot_root is treated as ambiguous rather than reconstructed.
        if TRACE_ROOT_CHANGE_RE.search(line):
            return "TRACE_AMBIGUOUS"
        if TRACE_MOUNT_TOPOLOGY_RE.search(line):
            if _trace_setup_mount_allowed(line):
                # The fixed namespace wrapper emits these records before the
                # evidence command starts. Their source/filesystem strings are
                # not subject-tree path arguments, so do not scan them again.
                continue
            return "TRACE_AMBIGUOUS"
        # Rename/link/symlink mutations can create a new alias for an unsafe
        # path or move an unsafe ancestor so later path attribution no longer
        # matches the exact materialized tree. Runtime evidence is read-only,
        # so any such topology mutation is ambiguous while unsafe entries exist.
        if TRACE_PATH_TOPOLOGY_RE.search(line):
            return "TRACE_AMBIGUOUS"
        # io_uring can perform pathname operations without a pathname-taking
        # syscall visible to strace %file. Because the tracee inherits no ring
        # descriptor, observing any io_uring setup/use means path attribution
        # is no longer complete enough for terminal evidence.
        if TRACE_IO_URING_RE.search(line):
            return "TRACE_AMBIGUOUS"
        # seccomp can defer a pathname syscall after ptrace entry and allow
        # another task to mutate the tracee buffer before kernel resolution.
        # Reject any seccomp syscall; the trace boundary cannot prove the
        # pathname bytes remained stable after they were observed.
        if TRACE_SECCOMP_RE.search(line):
            return "TRACE_AMBIGUOUS"
        if TRACE_PRCTL_RE.search(line):
            if "PR_SET_SECCOMP" in line:
                return "TRACE_AMBIGUOUS"
            # Other prctl string arguments (for example PR_SET_NAME) are
            # process metadata, not subject-tree paths.
            continue
        # Any CLONE_UNTRACED request can create a descendant outside strace -f
        # coverage. Seeing the flag is enough to invalidate terminal evidence.
        if "CLONE_UNTRACED" in line:
            return "TRACE_AMBIGUOUS"
        # Relative paths after a cwd change cannot be bound to the original
        # subject root without reconstructing process state. Fail closed.
        if "chdir(" in line or "fchdir(" in line:
            return "TRACE_AMBIGUOUS"

        for match in TRACE_DIRFD_PATH_RE.finditer(line):
            decoded = _decode_trace_string(match.group(2))
            if decoded is None:
                return "TRACE_AMBIGUOUS"
            if decoded and not os.path.isabs(decoded) and match.group(1).strip() != "AT_FDCWD":
                return "TRACE_AMBIGUOUS"

        for raw in TRACE_QUOTED_RE.findall(line):
            decoded = _decode_trace_string(raw)
            if decoded is None:
                return "TRACE_AMBIGUOUS"
            if not decoded or "\x00" in decoded:
                continue
            if os.path.isabs(decoded):
                normalized = _normalize_trace_path(decoded)
            else:
                # strace reports AT_FDCWD-relative names exactly as supplied.
                # Resolve them against the fixed subject cwd before deciding
                # whether they escape. This catches aliases such as
                # ../tree/optional-config that normalize back into the root.
                normalized = _normalize_trace_path(os.path.join(base, decoded))
            if _trace_path_alias_ambiguous(normalized):
                return "TRACE_AMBIGUOUS"
            if normalized == base:
                relative = "."
            elif normalized.startswith(base + os.sep):
                relative = os.path.relpath(normalized, base)
            else:
                continue
            relative_posix = Path(relative).as_posix()
            for blocked in unsafe:
                if relative_posix == blocked or relative_posix.startswith(blocked + "/"):
                    return "UNSAFE_TREE_DEPENDENCY"
    return None


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
    temporary, work, unsafe_paths = checked_out
    if not _executable_arguments_bound(trusted, work):
        _release_subject_tree(temporary)
        return "EXECUTION_FAILED", "CODE_UNBOUND", b""

    trace_enabled = bool(unsafe_paths)
    trace_read_fd: int | None = None
    trace_write_fd: int | None = None
    pass_fds: tuple[int, ...] = ()
    exec_argv = trusted
    if trace_enabled:
        tracer = _trusted_trace_argv()
        unshare = _trusted_argv(["unshare"])
        launcher = _trusted_argv(["python3", "-c", TRACE_TARGET_LAUNCHER])
        collector = _trusted_argv(["dd"])
        if (
            tracer is None
            or unshare is None
            or launcher is None
            or collector is None
            or not _trace_isolation_supported()
        ):
            _release_subject_tree(temporary)
            return "EXECUTION_FAILED", "TRACE_SETUP_UNAVAILABLE", b""
        trace_read_fd, trace_write_fd = os.pipe()
        os.set_inheritable(trace_write_fd, True)
        collector_command = "|" + shlex.join(
            [*collector, f"of=/proc/self/fd/{trace_write_fd}", "status=none"]
        )
        # The tracee runs in a private PID/proc namespace and a fixed launcher
        # closes every inherited descriptor above stderr before the real command.
        # The strace pipe collector stays outside that namespace, so the tracee
        # cannot discover, drain, replace, or forge the observation channel.
        exec_argv = [
            *tracer,
            "-f",
            "-qq",
            "-s",
            "4096",
            "-e",
            TRACE_SYSCALL_FILTER,
            "-o",
            collector_command,
            "--",
            *unshare,
            "-c",
            "-p",
            "-f",
            "--mount-proc",
            "--",
            *launcher,
            *trusted,
        ]
        pass_fds = (trace_write_fd,)

    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            exec_argv,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=True,
            env=_execution_environment(),
            pass_fds=pass_fds,
        )
        if trace_write_fd is not None:
            os.close(trace_write_fd)
            trace_write_fd = None
        stdout = proc.stdout
        if stdout is None:
            _stop_group(proc, stdout)
            return "EXECUTION_FAILED", "OUTPUT_PIPE", b""
        output_fd = stdout.fileno()
        os.set_blocking(output_fd, False)
        trace_fd = trace_read_fd
        if trace_fd is not None:
            os.set_blocking(trace_fd, False)

        chunks: list[bytes] = []
        trace_chunks: list[bytes] = []
        total = 0
        trace_total = 0
        deadline = time.monotonic() + TIMEOUT_SECONDS
        open_fds: dict[int, str] = {output_fd: "output"}
        if trace_fd is not None:
            open_fds[trace_fd] = "trace"
        status = ""

        def absorb(kind: str, block: bytes) -> bool:
            nonlocal total, trace_total
            if kind == "trace":
                if trace_total + len(block) > TRACE_LIMIT_BYTES:
                    return False
                trace_chunks.append(block)
                trace_total += len(block)
                return True
            if total + len(block) > OUTPUT_LIMIT_BYTES:
                return False
            chunks.append(block)
            total += len(block)
            return True

        while not status:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                break
            exited = proc.poll() is not None
            if open_fds:
                readable, _, _ = select.select(
                    list(open_fds), [], [], 0 if exited else min(0.2, remaining)
                )
                for fd in readable:
                    try:
                        block = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    if not block:
                        open_fds.pop(fd, None)
                        continue
                    kind = open_fds.get(fd, "output")
                    if not absorb(kind, block):
                        status = "trace_limit" if kind == "trace" else "limit"
                        break
                if status:
                    break
                if readable:
                    continue
            if exited or proc.poll() is not None:
                status = "exited"
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

        if status == "exited" and open_fds:
            for fd in list(open_fds):
                kind = open_fds[fd]
                while True:
                    try:
                        block = os.read(fd, 4096)
                    except BlockingIOError:
                        break
                    if not block:
                        break
                    if not absorb(kind, block):
                        status = "trace_limit" if kind == "trace" else "limit"
                        break
                if status != "exited":
                    break

        if status in {"limit", "timeout", "trace_limit"}:
            if status == "limit":
                return "OUTPUT_UNBOUNDED", "OUTPUT_LIMIT", b""
            if status == "trace_limit":
                return "EXECUTION_FAILED", "TRACE_LIMIT", b""
            return "TIMEOUT", "TIMEOUT", b""
        try:
            code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return "TIMEOUT", "TIMEOUT", b""
        stdout.close()
        output = b"".join(chunks)
        if code != 0:
            return "EXECUTION_FAILED", f"EXIT_{code}", output
        if trace_enabled:
            trace = b"".join(trace_chunks).decode("utf-8", errors="replace")
            trace_reason = _trace_unsafe_access_reason(trace, work, unsafe_paths)
            if trace_reason is not None:
                return "EXECUTION_FAILED", trace_reason, b""
        return "CAPTURED", "OK", output
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "TIMEOUT", b""
    finally:
        if proc is not None:
            _stop_group(proc, proc.stdout)
        for descriptor in (trace_write_fd, trace_read_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        _release_subject_tree(temporary)

def _retention_destination(root: Path, incident_id: str, capture_id: str) -> Path:
    git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    if not git_dir:
        raise _INCIDENT.EvidenceBlocked("GIT_DIR_UNAVAILABLE")
    return _INCIDENT._retention_destination(Path(git_dir), incident_id, capture_id)


def _identity_matches(record: dict[str, Any], effect: dict[str, Any]) -> bool:
    return all(record.get(field) == effect.get(field) for field in IDENTITY_FIELDS)


def _stored_result(
    record: dict[str, Any],
    kind: str,
    capture_id: str,
    effect: dict[str, Any],
) -> dict[str, str] | None:
    state = str(record.get("result") or "")
    if state in {"", "RESERVED"}:
        return None
    if not _identity_matches(record, effect):
        return _result("UNAVAILABLE", "RETENTION_IDENTITY_MISMATCH", kind=kind, capture_id=capture_id)
    if state == "CAPTURED":
        raw = record.get("raw_output")
        if not isinstance(raw, str) or SENSITIVE_RE.search(raw):
            return _result(
                "BLOCKED_SENSITIVE_OUTPUT",
                "SENSITIVE_OUTPUT",
                kind=kind,
                capture_id=capture_id,
                content_digest=str(record.get("content_digest") or ""),
            )
    evidence_ref = ""
    if state == "CAPTURED":
        evidence_ref = _retention_ref(str(effect["incident_id"]), capture_id)
    return _result(
        state,
        "ALREADY_CAPTURED" if state == "CAPTURED" else str(record.get("reason") or state),
        kind=kind,
        capture_id=capture_id,
        evidence_ref=evidence_ref,
        content_digest=str(record.get("content_digest") or ""),
        executed=False,
    )


def _await_capture(destination: Path, kind: str, capture_id: str, effect: dict[str, Any]) -> dict[str, str]:
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
        stored = _stored_result(record, kind, capture_id, effect)
        if stored is not None:
            return stored
        if time.monotonic() >= deadline:
            return _result("UNAVAILABLE", "RESERVATION_PENDING", kind=kind, capture_id=capture_id)
        time.sleep(0.02)


def _claim_capture(
    root: Path,
    incident_id: str,
    capture_id: str,
    kind: str,
    effect: dict[str, Any],
) -> dict[str, str] | Path:
    destination = _retention_destination(root, incident_id, capture_id)
    if destination.is_symlink():
        raise _INCIDENT.EvidenceBlocked("RETENTION_PATH_ESCAPE")
    if destination.exists():
        return _await_capture(destination, kind, capture_id, effect)
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
        return _await_capture(destination, kind, capture_id, effect)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return destination


def _partial_destination(destination: Path) -> Path:
    name = destination.name
    if (
        not name.endswith(".json")
        or name.startswith(".")
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir():
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    if destination.is_symlink() or not destination.is_file():
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    try:
        if destination.resolve().parent != parent.resolve():
            raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    except OSError as exc:
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED") from exc
    temporary = parent / f".{name}.partial"
    if temporary.parent != parent or temporary.name != f".{name}.partial":
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
    return temporary


def _write_exclusive_nofollow(path: Path, payload: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED") from exc
    finally:
        os.close(descriptor)


def _finalize_capture(destination: Path, record: dict[str, Any]) -> str:
    temporary = _partial_destination(destination)
    payload = json.dumps(record, sort_keys=True).encode("utf-8")
    if len(payload) > _INCIDENT.MAX_RECORD_BYTES:
        raise _INCIDENT.EvidenceBlocked("RECORD_TOO_LARGE")
    try:
        _write_exclusive_nofollow(temporary, payload)
        os.replace(temporary, destination)
    except OSError as exc:
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED") from exc
    if destination.is_symlink() or not destination.is_file():
        raise _INCIDENT.EvidenceBlocked("RETENTION_UNTRUSTED")
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
        claim = _claim_capture(root, incident_id, capture_id, kind, effect)
    except _INCIDENT.EvidenceBlocked as exc:
        if exc.reason == "CAPTURE_ID_COLLISION":
            try:
                return _await_capture(
                    _retention_destination(root, incident_id, capture_id),
                    kind,
                    capture_id,
                    effect,
                )
            except _INCIDENT.EvidenceBlocked as nested:
                return _result("UNAVAILABLE", nested.reason, kind=kind, capture_id=capture_id)
        return _result("UNAVAILABLE", exc.reason, kind=kind, capture_id=capture_id)
    if isinstance(claim, dict):
        return claim

    try:
        status, exec_reason, output = _execute_once(root, subject_head, command)
    except subprocess.TimeoutExpired:
        status, exec_reason, output = "TIMEOUT", "TIMEOUT", b""
    except OSError:
        status, exec_reason, output = "EXECUTION_FAILED", "EXECUTION_FAILED", b""
    if status == "CAPTURED" and SENSITIVE_RE.search(output.decode("utf-8", errors="replace")):
        status = "BLOCKED_SENSITIVE_OUTPUT"
        exec_reason = "SENSITIVE_OUTPUT"
    digest = hashlib.sha256(output).hexdigest() if output else ""
    executed = exec_reason not in {
        "COMMAND_PARSE",
        "COMMAND_EMPTY",
        "SUBJECT_TREE",
        "EXECUTABLE_UNTRUSTED",
        "CODE_UNBOUND",
        "TRACE_SETUP_UNAVAILABLE",
    }
    terminal = {
        "schema_version": 1,
        "kind": "runtime-evidence",
        "incident_id": incident_id,
        "capture_id": capture_id,
        "evidence_kind": kind,
        "subject_head": subject_head,
        "canonical_field_or_capability": field,
        "command_sha256": command_sha,
        "target_repo": target_repo,
        "retention_subject": retention_subject,
        "result": status,
        "reason": exec_reason,
        "content_digest": digest,
        "mitigation_authority": "NONE",
    }
    if status == "CAPTURED":
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
