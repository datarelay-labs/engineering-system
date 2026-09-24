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
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
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
    completed = subprocess.run(
        ["git", "-C", str(root), "show", f"{rev}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
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
    if proc.poll() is None:
        try:
            getattr(os, "killpg")(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            proc.kill()
    proc.wait(timeout=2)
    close = getattr(stdout, "close", None)
    if close is not None:
        close()


def _subject_tree(root: Path, subject_head: str) -> tuple[tempfile.TemporaryDirectory[str], Path] | None:
    temporary = tempfile.TemporaryDirectory(prefix="runtime-evidence-exec-")
    work = Path(temporary.name) / "tree"
    added = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "--detach", str(work), subject_head],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if added.returncode != 0:
        temporary.cleanup()
        return None
    return temporary, work


def _release_subject_tree(root: Path, temporary: tempfile.TemporaryDirectory[str], work: Path) -> None:
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(work)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    temporary.cleanup()


def _execute_once(root: Path, subject_head: str, command: str) -> tuple[str, str, bytes]:
    try:
        argv = shlex.split(command)
    except ValueError:
        return "EXECUTION_FAILED", "COMMAND_PARSE", b""
    if not argv:
        return "EXECUTION_FAILED", "COMMAND_EMPTY", b""
    checked_out = _subject_tree(root, subject_head)
    if checked_out is None:
        return "EXECUTION_FAILED", "SUBJECT_TREE", b""
    temporary, work = checked_out
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=True,
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
            _stop_group(proc, stdout)
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
        if proc is not None and proc.poll() is None:
            _stop_group(proc, proc.stdout)
        _release_subject_tree(root, temporary, work)


def _retention_destination(root: Path, incident_id: str, capture_id: str) -> Path:
    git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    if not git_dir:
        raise _INCIDENT.EvidenceBlocked("GIT_DIR_UNAVAILABLE")
    return _INCIDENT._retention_destination(Path(git_dir), incident_id, capture_id)


def _retention_precheck(root: Path, incident_id: str, capture_id: str, kind: str) -> dict[str, str] | None:
    try:
        destination = _retention_destination(root, incident_id, capture_id)
        if destination.is_symlink():
            raise _INCIDENT.EvidenceBlocked("RETENTION_PATH_ESCAPE")
        if destination.is_file():
            record = json.loads(destination.read_text(encoding="utf-8"))
            return _result(
                str(record.get("result") or "CAPTURED"),
                "ALREADY_CAPTURED",
                kind=str(record.get("evidence_kind") or kind),
                capture_id=capture_id,
                evidence_ref=_retention_ref(incident_id, capture_id),
                content_digest=str(record.get("content_digest") or ""),
                executed=False,
            )
        _INCIDENT._enforce_bounds(destination, b"{}")
    except _INCIDENT.EvidenceBlocked as exc:
        return _result("UNAVAILABLE", exc.reason, kind=kind, capture_id=capture_id)
    return None


def _write_private(root: Path, incident_id: str, capture_id: str, record: dict[str, Any]) -> str:
    destination = _retention_destination(root, incident_id, capture_id)
    payload = json.dumps(record, sort_keys=True).encode("utf-8")
    _INCIDENT._enforce_bounds(destination, payload)
    _INCIDENT._write_record(destination, payload)
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

    existing = _retention_precheck(root, incident_id, capture_id, kind)
    if existing is not None:
        return existing

    status, exec_reason, output = _execute_once(root, subject_head, command)
    digest = hashlib.sha256(output).hexdigest() if output else ""
    executed = exec_reason not in {"COMMAND_PARSE", "COMMAND_EMPTY", "SUBJECT_TREE"}
    if status != "CAPTURED":
        return _result(status, exec_reason, kind=kind, capture_id=capture_id, content_digest=digest, executed=executed)
    if SENSITIVE_RE.search(output.decode("utf-8", errors="replace")):
        return _result(
            "BLOCKED_SENSITIVE_OUTPUT",
            "SENSITIVE_OUTPUT",
            kind=kind,
            capture_id=capture_id,
            content_digest=digest,
            executed=True,
        )
    record = {
        "schema_version": 1,
        "kind": "runtime-evidence",
        "incident_id": incident_id,
        "capture_id": capture_id,
        "evidence_kind": kind,
        "subject_head": subject_head,
        "result": "CAPTURED",
        "content_digest": digest,
        "raw_output": output.decode("utf-8", errors="replace"),
        "mitigation_authority": "NONE",
    }
    try:
        evidence_ref = _write_private(root, incident_id, capture_id, record)
    except _INCIDENT.EvidenceBlocked as exc:
        return _result(
            "UNAVAILABLE",
            exc.reason,
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
