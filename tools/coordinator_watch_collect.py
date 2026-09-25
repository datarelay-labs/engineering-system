#!/usr/bin/env python3
"""Bounded read-only collector for one coordinator watch.

Facts come from authenticated ``gh`` reads of the declared repository and
issue. Caller-selected JSON files are not a source. This module does not
mutate GitHub, accept a caller command or URL, or send notifications.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_RE = re.compile(r"^[0-9]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CI_FROM_GITHUB = {"success": "PASS", "pending": "PENDING", "failure": "FAIL", "error": "FAIL"}
TRUSTED_GH = Path("/usr/bin/gh")
TRUSTED_GIT = Path("/usr/bin/git")
TRUSTED_AGENT = Path("/usr/bin/agent")
WORKTREE_PIN = Path("/etc/engineering-system/coordinator-watch-worktree")
CLAIM_DIR = Path("/var/lib/engineering-system/coordinator-watch-host/claims")
ACTIVE_CLAIM_STATUSES = frozenset({"CLAIMED", "ACTIVE", "WAIT", "YIELD", "RETRY"})
# Test-only location seams. Production never reads PATH, the request, or the environment for these.
_TEST_TRUSTED_GH: Path | None = None
_TEST_TRUSTED_GIT: Path | None = None
_TEST_TRUSTED_AGENT: Path | None = None
_TEST_WORKTREE: Path | None = None
_TEST_CLAIM_DIR: Path | None = None
_TEST_MEMINFO_BODIES: list[str] | None = None


class CollectError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _executable_provenance_ok(path: Path) -> bool:
    """Root-owned regular file, not a symlink, and not group/world-writable."""
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        if not os.access(path, os.X_OK):
            return False
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            return False
    except OSError:
        return False
    return True


def _directory_provenance_ok(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            return False
    except OSError:
        return False
    return True


def resolve_trusted_executable(production: Path, override: Path | None) -> Path | None:
    """Resolve one fixed binary. Caller PATH is never searched."""
    if override is not None:
        path = Path(override)
        try:
            st = path.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
            return None
        return path
    if not _executable_provenance_ok(production):
        return None
    return production


def resolve_trusted_gh() -> Path | None:
    return resolve_trusted_executable(TRUSTED_GH, _TEST_TRUSTED_GH)


def _bounded_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    home = os.environ.get("HOME", "")
    if home and "\x00" not in home and "\n" not in home:
        env["HOME"] = home
    # Test double state file. Production gh ignores this name; it does not select the binary.
    state = os.environ.get("WATCH_HOST_GH_STATE", "")
    if state and "\x00" not in state and "\n" not in state:
        env["WATCH_HOST_GH_STATE"] = state
    return env


def _run_gh(args: list[str]) -> Any:
    if not args or any(not isinstance(arg, str) or arg == "" or "\x00" in arg or "\n" in arg for arg in args):
        raise CollectError("collector refused an unbounded gh argument")
    binary = resolve_trusted_gh()
    if binary is None:
        raise CollectError("trusted gh provenance is unavailable")
    try:
        completed = subprocess.run(
            [str(binary), *args],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
            env=_bounded_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectError("authenticated gh read failed") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise CollectError("authenticated gh read failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CollectError("authenticated gh read was not JSON") from exc


def _parse_body(body: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    dependencies: list[dict[str, str]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "DEPENDENCY" and ":" in value:
            workstream, status = value.split(":", 1)
            dependencies.append({"workstream": workstream.strip(), "status": status.strip().upper()})
            continue
        if key and key not in fields:
            fields[key] = value
    fields["_dependencies"] = dependencies  # type: ignore[assignment]
    return fields


def _required(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectError(f"authoritative packet is missing {key}")
    return value.strip()


def _bool_line(fields: dict[str, Any], key: str, default: bool) -> bool:
    if key not in fields:
        return default
    value = str(fields[key]).lower()
    if value not in {"true", "false"}:
        raise CollectError(f"authoritative packet field {key} is not a boolean")
    return value == "true"


def _run_observed(binary: Path, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [str(binary), *args],
            cwd=None if cwd is None else str(cwd),
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
            env=_bounded_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _pinned_worktree() -> Path | None:
    if _TEST_WORKTREE is not None:
        path = Path(_TEST_WORKTREE)
        if path.is_symlink() or not path.is_dir():
            return None
        return path
    if not _file_pin_ok(WORKTREE_PIN):
        return None
    return _worktree_from_pin(WORKTREE_PIN)


def _file_pin_ok(path: Path) -> bool:
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        parent = path.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            return False
    except OSError:
        return False
    return True


def _worktree_from_pin(pin: Path) -> Path | None:
    try:
        text = pin.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or "\n" in text or "\x00" in text or not text.startswith("/"):
        return None
    path = Path(text)
    if path.is_symlink() or not path.is_dir():
        return None
    return path


def _git_stdout(worktree: Path, args: list[str]) -> str | None:
    binary = resolve_trusted_executable(TRUSTED_GIT, _TEST_TRUSTED_GIT)
    if binary is None:
        return None
    completed = _run_observed(binary, ["-C", str(worktree), *args])
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout


def _origin_matches(worktree: Path, repository: str) -> bool:
    url = _git_stdout(worktree, ["remote", "get-url", "origin"])
    return bool(url and repository in url.strip())


def _observe_git(worktree: Path, repository: str) -> tuple[bool | None, bool | None]:
    """Return dirty, unpushed. Unobserved values stay None, never a passing false."""
    if not _origin_matches(worktree, repository):
        return None, None
    status = _git_stdout(worktree, ["status", "--porcelain", "--untracked-files=all"])
    dirty = None if status is None else bool(status.strip())
    upstream = _git_stdout(worktree, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    if upstream is None:
        return dirty, None
    count = _git_stdout(worktree, ["rev-list", "--count", "@{upstream}..HEAD"])
    if count is None or not count.strip().isdigit():
        return dirty, None
    return dirty, int(count.strip()) > 0


def _parse_sessions(text: str) -> list[dict[str, str]] | None:
    lowered = text.lower()
    if "no cursor-managed persistent sessions" in lowered or "no persistent sessions" in lowered:
        if "workspace:" not in lowered:
            return []
    sessions: list[dict[str, str]] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if current.get("workspace"):
            sessions.append(dict(current))
        current.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Task:"):
            flush()
            continue
        if line.startswith("Status:"):
            current["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("Workspace:"):
            current["workspace"] = line.split(":", 1)[1].strip()
    flush()
    if not sessions and "persistent session" not in lowered:
        return None
    return sessions


def _observe_sessions() -> list[dict[str, str]] | None:
    binary = resolve_trusted_executable(TRUSTED_AGENT, _TEST_TRUSTED_AGENT)
    if binary is None:
        return None
    completed = _run_observed(binary, ["persist", "list"])
    if completed is None or completed.returncode != 0:
        return None
    return _parse_sessions(completed.stdout)


def _read_meminfo() -> str | None:
    bodies = _TEST_MEMINFO_BODIES
    if bodies is not None:
        if not bodies:
            return None
        if len(bodies) > 1:
            return bodies.pop(0)
        return bodies[0]
    try:
        return Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None


def _resource_module():
    path = Path(__file__).resolve().with_name("cursor-resource-preflight.py")
    spec = importlib.util.spec_from_file_location("cursor_resource_preflight", path)
    if spec is None or spec.loader is None:
        raise CollectError("resource preflight module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observe_resource(session_count: int | None) -> str:
    if session_count is None:
        return "UNKNOWN"
    text = _read_meminfo()
    if text is None:
        return "UNKNOWN"
    try:
        module = _resource_module()
        meminfo = module.parse_meminfo(text)
        thresholds = module.apply_override(meminfo["MemTotal"], None, "builtin")
        return str(module.evaluate(meminfo, session_count, thresholds)["RESULT"])
    except Exception:
        return "UNKNOWN"


def _claim_dir() -> Path | None:
    if _TEST_CLAIM_DIR is not None:
        path = Path(_TEST_CLAIM_DIR)
        if path.is_symlink() or not path.is_dir():
            return None
        return path
    if not _directory_provenance_ok(CLAIM_DIR):
        return None
    return CLAIM_DIR


def _matching_claim(directory: Path, repository: str, workstream: str, intent_revision: int) -> bool | None:
    try:
        paths = sorted(directory.iterdir())
    except OSError:
        return None
    matched = False
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        status = str(payload.get("status") or "").upper()
        if payload.get("repository") != repository or payload.get("workstream") != workstream:
            continue
        if payload.get("intent_revision") != intent_revision or status not in ACTIVE_CLAIM_STATUSES:
            continue
        if payload.get("ambiguous") is True:
            return None
        matched = True
    return matched


def _observe_admission(resource: str, repository: str, workstream: str, intent_revision: int) -> dict[str, Any]:
    if resource == "UNKNOWN":
        return {"decision": "UNKNOWN"}
    if resource == "BLOCK":
        return {"decision": "DENY", "deny_class": "HOST_BUDGET"}
    directory = _claim_dir()
    if directory is None:
        return {"decision": "UNKNOWN"}
    matched = _matching_claim(directory, repository, workstream, intent_revision)
    if matched is None:
        return {"decision": "UNKNOWN"}
    if matched:
        return {"decision": "ALLOW"}
    return {"decision": "UNKNOWN"}


def _observe_worker(
    sessions: list[dict[str, str]] | None,
    worktree: Path | None,
    repository: str,
    intent_revision: int,
    dirty: bool | None,
) -> dict[str, Any] | None:
    """Return worker facts from a persist-list observation. None means unobserved."""
    if sessions is None:
        return None
    if worktree is None:
        return {"present": False}
    matched = False
    for session in sessions:
        workspace = Path(session.get("workspace") or "")
        try:
            same = workspace.resolve() == worktree.resolve()
        except OSError:
            same = False
        if same and _origin_matches(worktree, repository):
            matched = True
            break
    if not matched:
        return {"present": False}
    return {
        "present": True,
        "status": "ACTIVE",
        "starting_intent_revision": intent_revision,
        "progress_evidence": dirty is True,
    }


def collect_authoritative(
    *,
    repository: str,
    workstream: str,
    issue_id: str,
    watch_class: str,
) -> dict[str, Any]:
    """Return ``{"branch", "facts"}`` from gh. Fails closed when the read is incomplete."""
    if not REPOSITORY_RE.fullmatch(repository) or not ISSUE_RE.fullmatch(issue_id):
        raise CollectError("collector identity is not a repository and issue number")
    issue = _run_gh(["issue", "view", issue_id, "--repo", repository, "--json", "number,body"])
    if not isinstance(issue, dict) or str(issue.get("number")) != issue_id:
        raise CollectError("gh issue identity does not match the declared issue")
    body = issue.get("body")
    if not isinstance(body, str):
        raise CollectError("gh issue body is missing")
    fields = _parse_body(body)
    packet_repo = _required(fields, "TARGET_REPO")
    packet_workstream = _required(fields, "WORKSTREAM")
    if packet_repo != repository or packet_workstream != workstream:
        raise CollectError("gh issue packet identity does not match the declared watch")
    branch = _required(fields, "BRANCH")
    if not BRANCH_RE.fullmatch(branch):
        raise CollectError("authoritative branch is not a bounded git ref")
    status = _required(fields, "STATUS").upper()
    try:
        intent_revision = int(_required(fields, "INTENT_REVISION"))
    except ValueError as exc:
        raise CollectError("authoritative intent revision is not an integer") from exc
    if intent_revision < 1:
        raise CollectError("authoritative intent revision is not an integer")
    encoded_branch = urllib.parse.quote(branch, safe="")
    commit = _run_gh(["api", f"repos/{repository}/commits/{encoded_branch}"])
    sha = str(commit.get("sha") or "").lower() if isinstance(commit, dict) else ""
    if not SHA_RE.fullmatch(sha):
        raise CollectError("authoritative branch head is not an exact git sha")
    status_doc = _run_gh(["api", f"repos/{repository}/commits/{sha}/status"])
    github_state = str(status_doc.get("state") or "").lower() if isinstance(status_doc, dict) else ""
    ci_state = CI_FROM_GITHUB.get(github_state, "UNKNOWN")
    pulls = _run_gh(["pr", "list", "--repo", repository, "--head", branch, "--json", "number,state,headRefOid", "--limit", "1"])
    if not isinstance(pulls, list):
        raise CollectError("authenticated pull request read was not a list")
    pr_exists = bool(pulls)
    pr_head = sha
    if pr_exists:
        head_oid = str(pulls[0].get("headRefOid") or "").lower()
        if head_oid != sha:
            raise CollectError("pull request head does not match the authoritative branch head")
        pr_state = str(pulls[0].get("state") or "").upper()
    else:
        pr_state = ""
    worktree = _pinned_worktree()
    sessions = _observe_sessions()
    dirty, unpushed = _observe_git(worktree, repository) if worktree is not None else (None, None)
    worker = _observe_worker(sessions, worktree, repository, intent_revision, dirty)
    resource = _observe_resource(None if sessions is None else len(sessions))
    admission = _observe_admission(resource, repository, workstream, intent_revision)
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git_facts: dict[str, Any] = {"head": sha, "yielded_for_audit": False}
    if dirty is not None:
        git_facts["dirty"] = dirty
    if unpushed is not None:
        git_facts["unpushed"] = unpushed
    facts: dict[str, Any] = {
        "packet": {
            "repository": repository,
            "workstream": workstream,
            "status": status,
            "priority": _required(fields, "PRIORITY").upper(),
            "intent_revision": intent_revision,
            "change_risk": _required(fields, "CHANGE_RISK").upper(),
            "task_kind": _required(fields, "TASK_KIND").upper(),
            "dependencies": fields["_dependencies"],
        },
        "git": git_facts,
        "resource": {"result": resource},
        "admission": admission,
        "mutation": {"ambiguous": _bool_line(fields, "MUTATION_AMBIGUOUS", False)},
        "failure": {"class": "NONE", "identical_semantic_count": 0},
        "publication": {"coordinator_audit": str(fields.get("PUBLICATION_AUDIT") or "UNKNOWN").upper()},
        "pr": {"exists": pr_exists},
        "ci": {"state": ci_state, "subject_head": sha},
        "review": {"actionable_open": False, "state": "UNKNOWN"},
        "wait": {"external": False, "retry_count": 0, "retry_budget": 3},
        "gates": {"org_rollout": "NOT_REQUIRED"},
        "watch": {"watch_class": watch_class, "observed_at": observed_at},
    }
    if worker is not None:
        facts["worker"] = worker
    if str(fields.get("PUBLICATION_REVISION") or "").strip():
        facts["publication"]["authorized_intent_revision"] = int(str(fields["PUBLICATION_REVISION"]))
    if pr_exists:
        facts["pr"] = {"exists": True, "state": pr_state, "head": pr_head, "mergeable": "UNKNOWN"}
    return {"branch": branch, "facts": facts}
