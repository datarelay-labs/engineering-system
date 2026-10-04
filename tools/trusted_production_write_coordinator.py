#!/usr/bin/env python3
"""Root host coordinator for one exact production-write assertion issuance."""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import pwd
import ssl
import stat
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

import trusted_external_write_coordinator as ext
from production_approver_policy import (
    PolicyError,
    canonical_json_sha256,
    find_exact_approval,
    load_policy_bytes,
    packet_sha256,
)

SIGNER = Path("/usr/lib/engineering-system/trusted-production-write-signer")
APPROVERS = Path("/etc/engineering-system/production-approvers.json")
PUBLIC_GITHUB_HOST = "api.github.com"
SYSTEM_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
MAX_PUBLIC_JSON = 256 * 1024
MAX_CA_BUNDLE_BYTES = 4 * 1024 * 1024


class Error(Exception):
    pass


def _direct_root_without_operator() -> bool:
    if os.geteuid() != 0:
        return False
    sudo_uid = os.environ.get("SUDO_UID", "")
    if not sudo_uid:
        return True
    if not sudo_uid.isdigit():
        raise Error("SUDO_UID is invalid")
    try:
        entry = pwd.getpwuid(int(sudo_uid))
    except (KeyError, ValueError) as exc:
        raise Error("invoking user identity is unavailable") from exc
    return entry.pw_uid == 0 or Path(entry.pw_dir) == Path("/root")


def _load_approver_policy() -> tuple[dict[str, Any], ...]:
    try:
        st = APPROVERS.lstat()
        parent = APPROVERS.parent.lstat()
    except OSError as exc:
        raise Error("production approver policy unavailable") from exc
    if (
        not stat.S_ISREG(st.st_mode)
        or stat.S_ISLNK(st.st_mode)
        or st.st_uid != 0
        or stat.S_IMODE(st.st_mode) != 0o600
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or (parent.st_mode & 0o022) != 0
    ):
        raise Error("production approver policy provenance invalid")
    try:
        fd = os.open(APPROVERS, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            raw = os.read(fd, 32 * 1024 + 1)
        finally:
            os.close(fd)
        return load_policy_bytes(raw)
    except (OSError, PolicyError) as exc:
        raise Error(str(exc)) from exc


def _trusted_ca_bundle() -> Path:
    try:
        st = SYSTEM_CA_BUNDLE.lstat()
        parent = SYSTEM_CA_BUNDLE.parent.lstat()
    except OSError as exc:
        raise Error("system CA bundle unavailable") from exc
    if (
        not stat.S_ISREG(st.st_mode)
        or stat.S_ISLNK(st.st_mode)
        or st.st_uid != 0
        or (st.st_mode & 0o022) != 0
        or st.st_size <= 0
        or st.st_size > MAX_CA_BUNDLE_BYTES
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or (parent.st_mode & 0o022) != 0
    ):
        raise Error("system CA bundle provenance invalid")
    return SYSTEM_CA_BUNDLE


def _public_ssl_context() -> ssl.SSLContext:
    ca_bundle = _trusted_ca_bundle()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_verify_locations(cafile=str(ca_bundle))
    except (OSError, ssl.SSLError) as exc:
        raise Error("system CA bundle load failed") from exc
    return context


def _public_json(path: str) -> Any:
    if (
        not path.startswith("/repos/")
        or "://" in path
        or any(char in path for char in "\r\n\x00")
        or len(path) > 2048
    ):
        raise Error("public GitHub request path is invalid")
    conn = http.client.HTTPSConnection(
        PUBLIC_GITHUB_HOST,
        443,
        timeout=10,
        context=_public_ssl_context(),
    )
    try:
        conn.request(
            "GET",
            path,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "datarelay-engineering-system",
            },
        )
        response = conn.getresponse()
        if response.status != 200:
            raise Error("public GitHub read failed")
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if int(length) > MAX_PUBLIC_JSON:
                    raise Error("public GitHub response is too large")
            except ValueError as exc:
                raise Error("public GitHub response length is invalid") from exc
        raw = response.read(MAX_PUBLIC_JSON + 1)
        if len(raw) > MAX_PUBLIC_JSON:
            raise Error("public GitHub response is too large")
    except (OSError, http.client.HTTPException) as exc:
        raise Error("public GitHub read failed") from exc
    finally:
        conn.close()
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Error("public GitHub read returned invalid JSON") from exc


def _public_blob(repository: str, path: str, ref: str, label: str) -> bytes:
    if ext.SHA_RE.fullmatch(ref) is None:
        raise Error(f"{label} ref is invalid")
    quoted_path = urllib.parse.quote(path, safe="/")
    doc = _public_json(f"/repos/{repository}/contents/{quoted_path}?ref={ref}")
    if (
        not isinstance(doc, dict)
        or doc.get("encoding") != "base64"
        or not isinstance(doc.get("content"), str)
    ):
        raise Error(f"{label} is unavailable")
    try:
        return base64.b64decode(doc["content"], validate=False)
    except ValueError as exc:
        raise Error(f"{label} is invalid") from exc


def _public_execution_profile(repository: str, subject_head: str) -> dict[str, Any]:
    try:
        project_bytes = _public_blob(
            repository,
            ".engineering/project.yaml",
            subject_head,
            "target Engineering System profile",
        )
        project = ext.yaml.safe_load(project_bytes.decode("utf-8")) or {}
    except (UnicodeError, ext.yaml.YAMLError) as exc:
        raise Error("target Engineering System profile is unavailable") from exc
    engineering = project.get("engineering_system") if isinstance(project, dict) else None
    if not isinstance(engineering, dict):
        raise Error("target Engineering System profile is invalid")
    mode = str(engineering.get("mode") or "")
    if mode == "adopted":
        baseline = str(engineering.get("baseline") or "").lower()
        if ext.SHA_RE.fullmatch(baseline) is None:
            raise Error("target Engineering System baseline is invalid")
    elif mode == "canonical" and repository == ext.CANONICAL_REPOSITORY:
        baseline = subject_head
    else:
        raise Error("target Engineering System mode is unsupported")
    canonical_bytes = _public_blob(
        ext.CANONICAL_REPOSITORY,
        ext.EXECUTION_PROFILE_PATH,
        baseline,
        "canonical execution profile",
    )
    target_bytes = _public_blob(
        repository,
        ext.EXECUTION_PROFILE_PATH,
        subject_head,
        "target execution profile",
    )
    if target_bytes != canonical_bytes:
        raise Error("target execution profile differs from immutable canonical baseline")
    try:
        return ext.load_profile_text(canonical_bytes.decode("utf-8"))
    except (UnicodeError, ext.ProfileError) as exc:
        raise Error("canonical execution profile is invalid") from exc


def _validate_packet(
    a: argparse.Namespace,
    issue: Any,
    *,
    branch_head: str,
    execution_profile: dict[str, Any],
    authority_login: str | None = None,
) -> tuple[str, dict[str, str]]:
    if (
        not isinstance(issue, dict)
        or str(issue.get("number")) != str(a.issue_id)
        or str(issue.get("state", "")).lower() != "open"
        or not str(issue.get("title", "")).startswith("[AI Work]")
    ):
        raise Error("active Work Packet unavailable")
    body = issue.get("body")
    if not isinstance(body, str):
        raise Error("Work Packet authority unavailable")
    if authority_login is None:
        user = issue.get("user")
        if not isinstance(user, dict):
            raise Error("Work Packet authority unavailable")
        login = str(user.get("login") or "")
    else:
        login = authority_login
    fields = ext._fields(body)
    required = {
        "TARGET_REPO": a.repository,
        "WORKSTREAM": a.workstream,
        "STATUS": "ACTIVE",
        "BRANCH": a.branch,
        "LAST_VERIFIED_HEAD": a.subject_head,
        "INTENT_REVISION": str(a.intent_revision),
    }
    for key, value in required.items():
        if fields.get(key) != value:
            raise Error(f"authoritative Work Packet mismatch: {key}")
    if fields.get("CHANGE_RISK") not in {"HIGH", "CRITICAL"}:
        raise Error("production write requires HIGH or CRITICAL risk")
    if branch_head.lower() != a.subject_head:
        raise Error("authoritative branch HEAD mismatch")
    profile_blocking, _ = ext.packet_authority(execution_profile, fields)
    if profile_blocking:
        raise Error(
            "Work Packet execution profile is not authorized: " + profile_blocking[0]
        )
    return login, fields


def _snapshot_request(payload: dict[str, Any]) -> tuple[Path, Path]:
    directory = Path(tempfile.mkdtemp(prefix="engineering-production-request-", dir="/tmp"))
    path = directory / "request.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return directory, path


def _invoke_signer(
    a: argparse.Namespace,
    signer: Path,
    *,
    permission: str,
    authority_basis: str | None = None,
) -> int:
    cmd = [
        str(signer),
        "--root",
        str(a.root),
        "--request-json",
        str(a.request_json),
        "--repository",
        a.repository,
        "--workstream",
        a.workstream,
        "--branch",
        a.branch,
        "--subject-head",
        a.subject_head,
        "--intent-revision",
        str(a.intent_revision),
        "--session-id",
        a.session_id,
        "--dispatch-id",
        a.dispatch_id,
        "--authority-permission",
        permission,
        "--ttl-seconds",
        str(a.ttl_seconds),
        "--binding-out",
        str(a.binding_out),
        "--dispatch-out",
        str(a.dispatch_out),
    ]
    if authority_basis is not None:
        cmd.extend(["--authority-basis", authority_basis])
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if cp.returncode:
        raise Error("trusted production signer denied issuance: " + cp.stdout.strip())
    return 0


def _authorize_authenticated(a: argparse.Namespace) -> int:
    gh = ext._exec_path(ext.TRUSTED_GH, None)
    signer = ext._exec_path(SIGNER, None)
    issue = ext._run_json(gh, ["api", f"repos/{a.repository}/issues/{a.issue_id}"])
    if not isinstance(issue, dict):
        raise Error("active Work Packet unavailable")
    body = issue.get("body")
    user = issue.get("user")
    if not isinstance(body, str) or not isinstance(user, dict):
        raise Error("Work Packet authority unavailable")
    login = str(user.get("login") or "")
    permission_doc = ext._run_json(
        gh, ["api", f"repos/{a.repository}/collaborators/{login}/permission"]
    )
    permission = (
        str(permission_doc.get("permission") or "").lower()
        if isinstance(permission_doc, dict)
        else ""
    )
    if permission not in {"write", "maintain", "admin"}:
        raise Error("Work Packet author permission insufficient")
    fields = ext._fields(body)
    if fields.get("CHANGE_RISK") not in {"HIGH", "CRITICAL"}:
        raise Error("production write requires HIGH or CRITICAL risk")
    branch = ext._run_json(gh, ["api", f"repos/{a.repository}/commits/{a.branch}"])
    branch_head = (
        str(branch.get("sha") or "").lower() if isinstance(branch, dict) else ""
    )
    profile = ext._canonical_execution_profile(gh, a.repository, a.subject_head)
    login, _ = _validate_packet(
        a, issue, branch_head=branch_head, execution_profile=profile
    )
    _invoke_signer(a, signer, permission=permission)
    print("TRUSTED_PRODUCTION_WRITE_COORDINATOR=PASS")
    print(f"AUTHOR={login}")
    print(f"AUTHOR_PERMISSION={permission}")
    print("AUTHORITY_BASIS=collaborator_permission")
    return 0


def _authorize_root_public(a: argparse.Namespace) -> int:
    policy = _load_approver_policy()
    repository_doc = _public_json(f"/repos/{a.repository}")
    if (
        not isinstance(repository_doc, dict)
        or repository_doc.get("private") is not False
        or str(repository_doc.get("visibility") or "") != "public"
    ):
        raise Error("root-only fallback requires a public repository")
    issue = _public_json(f"/repos/{a.repository}/issues/{a.issue_id}")
    if not isinstance(issue, dict) or not isinstance(issue.get("body"), str):
        raise Error("active Work Packet unavailable")
    try:
        request_raw = a.request_json.read_bytes()
        if not request_raw or len(request_raw) > 64 * 1024:
            raise Error("production request size is invalid")
        request_payload = json.loads(request_raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise Error("production request is invalid") from exc
    if not isinstance(request_payload, dict):
        raise Error("production request must be an object")
    approval = find_exact_approval(
        policy,
        repository=a.repository,
        issue_id=int(a.issue_id),
        workstream=a.workstream,
        branch=a.branch,
        subject_head=a.subject_head,
        intent_revision=a.intent_revision,
        session_id=a.session_id,
        dispatch_id=a.dispatch_id,
        packet_digest=packet_sha256(issue["body"]),
        request_digest=canonical_json_sha256(request_payload),
    )
    if approval is None:
        raise Error("exact root-admin production approval is unavailable")
    approved_by = str(approval["approved_by"])
    fields = ext._fields(issue["body"])
    if fields.get("CHANGE_RISK") not in {"HIGH", "CRITICAL"}:
        raise Error("production write requires HIGH or CRITICAL risk")
    branch_ref = urllib.parse.quote(a.branch, safe="")
    branch_doc = _public_json(f"/repos/{a.repository}/commits/{branch_ref}")
    branch_head = (
        str(branch_doc.get("sha") or "").lower()
        if isinstance(branch_doc, dict)
        else ""
    )
    if branch_head != a.subject_head:
        raise Error("authoritative branch HEAD mismatch")
    commit_doc = _public_json(f"/repos/{a.repository}/commits/{a.subject_head}")
    commit_head = (
        str(commit_doc.get("sha") or "").lower()
        if isinstance(commit_doc, dict)
        else ""
    )
    if commit_head != a.subject_head:
        raise Error("authoritative subject commit is unavailable")
    profile = _public_execution_profile(a.repository, a.subject_head)
    login, _ = _validate_packet(
        a,
        issue,
        branch_head=branch_head,
        execution_profile=profile,
        authority_login=approved_by,
    )
    signer = ext._exec_path(SIGNER, None)
    _invoke_signer(
        a,
        signer,
        permission="production_approver",
        authority_basis="production_approver_policy",
    )
    print("TRUSTED_PRODUCTION_WRITE_COORDINATOR=PASS")
    print(f"AUTHOR={login}")
    print("AUTHOR_PERMISSION=production_approver")
    print("AUTHORITY_BASIS=production_approver_policy")
    return 0


def authorize(a: argparse.Namespace) -> int:
    repo = a.repository
    if ext.REPO_RE.fullmatch(repo) is None or ext.ISSUE_RE.fullmatch(a.issue_id) is None:
        raise Error("invalid repository/issue identity")
    if ext.SHA_RE.fullmatch(a.subject_head) is None:
        raise Error("production write subject HEAD is invalid")
    if _direct_root_without_operator():
        return _authorize_root_public(a)
    return _authorize_authenticated(a)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in (
        "repository",
        "issue-id",
        "workstream",
        "branch",
        "subject-head",
        "session-id",
        "dispatch-id",
    ):
        p.add_argument("--" + name, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--request-json", type=Path, required=True)
    p.add_argument("--intent-revision", type=int, required=True)
    p.add_argument("--ttl-seconds", type=int, default=120)
    p.add_argument("--binding-out", type=Path, required=True)
    p.add_argument("--dispatch-out", type=Path, required=True)
    return p


def main():
    try:
        return authorize(parser().parse_args())
    except (Error, ext.CoordinatorError, OSError, PolicyError) as exc:
        print(f"TRUSTED_PRODUCTION_WRITE_COORDINATOR=BLOCK reason={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
