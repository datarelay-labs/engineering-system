#!/usr/bin/env python3
"""Fail-closed local Git binding logic for Chat-primary implementation.

This repository copy is parity/reference/test material and is not self-authenticating.
Authoritative pre-mutation use requires source fetched from an immutable canonical
baseline and executed directly by the external coordinator via isolated stdin using a
trusted interpreter (for Linux: controlled environment/cwd plus /usr/bin/python3 -I -),
or an equivalent host-administered immutable copy outside the worker-writable tree.
Direct CLI execution of this worker-writable repository file is reference-only and
must fail closed.

GitHub Work Packet and author-permission authority belongs to the external,
authenticated coordinator/connector. This logic performs no network or GitHub
read and cannot mint mutation authority. It only proves that coordinator-supplied
expected repository facts match one clean local worktree through a
host-administered, config-isolated Git executable.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKSTREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
CHANGE_RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
WORKTREE_IDENTITY_RE = re.compile(r"^[0-9]+:[0-9]+(?:,[0-9]+:[0-9]+)*$")
DIRECT_CHAT_IMPLEMENTER = "CHATGPT_CHAT"
AUTHORITY_BOUNDARY = "EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED"
TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)


class PreflightError(ValueError):
    pass


def fail(reason: str) -> None:
    print(f"IMPLEMENTATION_LOCAL_BINDING=BLOCK REASON={reason}")
    print("MUTATION_AUTHORITY=NO")
    raise SystemExit(2)


def _lexical_components(path: Path) -> list[Path]:
    if not path.is_absolute():
        return []
    parts = path.parts
    current = Path(parts[0])
    out = [current]
    for part in parts[1:]:
        current = current / part
        out.append(current)
    return out


def _lexical_absolute_path(value: str, *, reason: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value or "\n" in value or "\r" in value:
        raise PreflightError(reason)
    if not value.startswith("/") or os.path.normpath(value) != value:
        raise PreflightError(reason)
    return Path(value)


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PreflightError("WORKTREE_FD_BOUNDARY_UNAVAILABLE")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identity_tuple(value: str) -> tuple[int, int]:
    device, inode = value.split(":", 1)
    return int(device), int(inode)


def _expected_identity_records(expected: str) -> list[tuple[int, int]]:
    if WORKTREE_IDENTITY_RE.fullmatch(expected) is None:
        raise PreflightError("EXPECTED_WORKTREE_IDENTITY_INVALID")
    return [_identity_tuple(item) for item in expected.split(",")]


def _open_child_dir(parent_fd: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise PreflightError("WORKTREE_PATH_INVALID")
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PreflightError("WORKTREE_PATH_UNAVAILABLE") from exc
    if stat.S_ISLNK(before.st_mode):
        raise PreflightError("WORKTREE_PATH_SYMLINK")
    if not stat.S_ISDIR(before.st_mode):
        raise PreflightError("WORKTREE_PATH_NOT_DIRECTORY")
    try:
        child_fd = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise PreflightError("WORKTREE_PATH_UNAVAILABLE") from exc
    after = os.fstat(child_fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(child_fd)
        raise PreflightError("WORKTREE_IDENTITY_MISMATCH")
    return child_fd


def _walk_worktree(path: Path) -> tuple[int, list[tuple[int, int]]]:
    """Open a no-follow directory chain and return the bound leaf fd + identities."""
    components = path.parts
    if not path.is_absolute() or not components or components[0] != "/":
        raise PreflightError("WORKTREE_PATH_INVALID")
    try:
        fd = os.open("/", _directory_open_flags())
    except OSError as exc:
        raise PreflightError("WORKTREE_FD_BOUNDARY_UNAVAILABLE") from exc
    records: list[tuple[int, int]] = []
    try:
        st = os.fstat(fd)
        records.append((st.st_dev, st.st_ino))
        for name in components[1:]:
            child_fd = _open_child_dir(fd, name)
            os.close(fd)
            fd = child_fd
            st = os.fstat(fd)
            records.append((st.st_dev, st.st_ino))
        return fd, records
    except Exception:
        os.close(fd)
        raise


def _format_identity(records: list[tuple[int, int]]) -> str:
    return ",".join(f"{device}:{inode}" for device, inode in records)


def worktree_identity(path: Path) -> str:
    """Capture a race-resistant no-follow device/inode chain for the lexical path."""
    fd, records = _walk_worktree(path)
    os.close(fd)
    return _format_identity(records)


def open_bound_worktree(path: Path, expected: str) -> int:
    """Open the exact coordinator-captured worktree identity and keep its inode bound."""
    expected_records = _expected_identity_records(expected)
    fd, actual_records = _walk_worktree(path)
    if actual_records != expected_records:
        os.close(fd)
        raise PreflightError("WORKTREE_IDENTITY_MISMATCH")
    return fd


def verify_worktree_identity(path: Path, expected: str) -> None:
    expected_records = _expected_identity_records(expected)
    fd, actual_records = _walk_worktree(path)
    os.close(fd)
    if actual_records != expected_records:
        raise PreflightError("WORKTREE_IDENTITY_MISMATCH")


def _fd_path(fd: int) -> Path:
    for base in (Path("/proc/self/fd"), Path("/dev/fd")):
        if base.is_dir():
            return base / str(fd)
    raise PreflightError("WORKTREE_FD_BOUNDARY_UNAVAILABLE")


def _open_relative_dir_fd(parent_fd: int, relative: str) -> int:
    path = Path(relative)
    if path.is_absolute() or not path.parts:
        raise PreflightError("SUBMODULE_STATE_UNAVAILABLE")
    fd = os.dup(parent_fd)
    try:
        for name in path.parts:
            child_fd = _open_child_dir(fd, name)
            os.close(fd)
            fd = child_fd
        return fd
    except Exception:
        os.close(fd)
        raise

def _root_administered_path(path: Path, *, executable: bool) -> bool:
    """Require root-owned, worker-inaccessible lexical and resolved path components."""
    if os.geteuid() == 0:
        return False
    try:
        lexical = path.absolute()
        for component in _lexical_components(lexical):
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode):
                return False
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
            if os.geteuid() != 0 and os.access(component, os.W_OK):
                return False

        resolved = lexical.resolve(strict=True)
        for component in _lexical_components(resolved):
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode):
                return False
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
            if os.geteuid() != 0 and os.access(component, os.W_OK):
                return False

        final = resolved.lstat()
        if not stat.S_ISREG(final.st_mode):
            return False
        if executable and not os.access(resolved, os.X_OK):
            return False
    except OSError:
        return False
    return True


def resolve_trusted_git() -> Path | None:
    for candidate in TRUSTED_GIT_CANDIDATES:
        if _root_administered_path(candidate, executable=True):
            return candidate.resolve()
    return None


def _bounded_git_env() -> dict[str, str]:
    """Environment for local identity reads; inherit no caller Git configuration."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def git(root: Path, *args: str, pass_fds: tuple[int, ...] = ()) -> str:
    binary = resolve_trusted_git()
    if binary is None:
        raise PreflightError("LOCAL_GIT_BOUNDARY_UNAVAILABLE")
    command = [
        str(binary),
        "-C",
        str(root),
        "--work-tree",
        str(root),
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        "submodule.recurse=false",
        *args,
    ]
    result = subprocess.run(
        command,
        cwd="/",
        env=_bounded_git_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=pass_fds,
    )
    if result.returncode:
        raise PreflightError("GIT_STATE_UNAVAILABLE")
    # Git path-like output may legitimately end in spaces. Remove only the
    # command line terminator; never use strip(), which can change repository
    # path identity (for example an initialized submodule named "z ").
    stdout = result.stdout
    if stdout.endswith("\r\n"):
        return stdout[:-2]
    if stdout.endswith("\n"):
        return stdout[:-1]
    return stdout


def normalize_origin(url: str) -> tuple[str, str]:
    raw = url.strip()
    if not raw or any(ch in raw for ch in "\r\n\0"):
        raise PreflightError("ORIGIN_INVALID")
    if "://" in raw:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lstrip("/")
    elif raw.startswith("git@") and ":" in raw:
        user_host, path = raw.split(":", 1)
        host = user_host.split("@", 1)[1].lower()
    else:
        raise PreflightError("ORIGIN_INVALID")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or REPO_RE.fullmatch(path) is None:
        raise PreflightError("ORIGIN_INVALID")
    return host, path


def _index_has_hidden_flags(root: Path, *, pass_fds: tuple[int, ...] = ()) -> bool:
    records = git(root, "ls-files", "-v", "-z", pass_fds=pass_fds).split("\0")
    for record in records:
        if not record:
            continue
        tag = record[0]
        if tag == "S" or tag.islower():
            return True
    return False


def _initialized_gitlink_paths(
    root: Path,
    *,
    pass_fds: tuple[int, ...] = (),
) -> list[str]:
    raw = git(root, "ls-files", "--stage", "-z", pass_fds=pass_fds)
    submodules: list[str] = []
    for record in raw.split("\0"):
        if not record:
            continue
        metadata, separator, relative = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise PreflightError("GIT_STATE_UNAVAILABLE")
        if fields[0] != "160000":
            continue
        rel = Path(relative)
        if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
            raise PreflightError("SUBMODULE_STATE_UNAVAILABLE")
        submodules.append(relative)
    return submodules


def _reject_hidden_index_flags_fd(root_fd: int, seen: set[tuple[int, int]] | None = None) -> None:
    visited = seen if seen is not None else set()
    st = os.fstat(root_fd)
    identity = (st.st_dev, st.st_ino)
    if identity in visited:
        raise PreflightError("SUBMODULE_STATE_UNAVAILABLE")
    visited.add(identity)
    root_ref = _fd_path(root_fd)
    if _index_has_hidden_flags(root_ref, pass_fds=(root_fd,)):
        raise PreflightError("HIDDEN_INDEX_STATE")
    for relative in _initialized_gitlink_paths(root_ref, pass_fds=(root_fd,)):
        child_fd = _open_relative_dir_fd(root_fd, relative)
        try:
            child_ref = _fd_path(child_fd)
            prefix = git(
                child_ref,
                "rev-parse",
                "--show-prefix",
                pass_fds=(child_fd,),
            )
            if prefix:
                raise PreflightError("SUBMODULE_STATE_UNAVAILABLE")
            _reject_hidden_index_flags_fd(child_fd, visited)
        finally:
            os.close(child_fd)


def require_clean_root(root: Path, worktree_fd: int) -> tuple[str, str, str]:
    root_ref = _fd_path(worktree_fd)
    prefix = git(
        root_ref,
        "rev-parse",
        "--show-prefix",
        pass_fds=(worktree_fd,),
    )
    if prefix:
        raise PreflightError("WORKTREE_ROOT_MISMATCH")
    branch = git(
        root_ref,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        pass_fds=(worktree_fd,),
    )
    if not branch:
        raise PreflightError("DETACHED_HEAD")
    head = git(
        root_ref,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        pass_fds=(worktree_fd,),
    )
    if HEAD_RE.fullmatch(head) is None:
        raise PreflightError("HEAD_INVALID")
    _reject_hidden_index_flags_fd(worktree_fd)
    if git(
        root_ref,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
        pass_fds=(worktree_fd,),
    ):
        raise PreflightError("WORKTREE_DIRTY")
    origin = git(
        root_ref,
        "config",
        "--local",
        "--no-includes",
        "--get",
        "remote.origin.url",
        pass_fds=(worktree_fd,),
    )
    return branch, head, origin


def _safe_branch(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 255
        and value == value.strip()
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    )


def check(args: argparse.Namespace) -> int:
    root = _lexical_absolute_path(args.root, reason="ROOT_PATH_INVALID")
    expected_worktree = _lexical_absolute_path(
        args.expected_worktree,
        reason="EXPECTED_WORKTREE_INVALID",
    )
    if root != expected_worktree:
        raise PreflightError("WORKTREE_BINDING_MISMATCH")
    if REPO_RE.fullmatch(args.expected_repo) is None:
        raise PreflightError("EXPECTED_REPO_INVALID")
    if WORKSTREAM_RE.fullmatch(args.expected_workstream) is None:
        raise PreflightError("WORKSTREAM_INVALID")
    if not _safe_branch(args.expected_branch):
        raise PreflightError("EXPECTED_BRANCH_INVALID")
    if HEAD_RE.fullmatch(args.expected_head) is None:
        raise PreflightError("EXPECTED_HEAD_INVALID")
    if args.issue_number < 1:
        raise PreflightError("WORK_PACKET_ISSUE_INVALID")
    if args.expected_intent_revision < 1:
        raise PreflightError("INTENT_REVISION_INVALID")

    worktree_fd = open_bound_worktree(root, args.expected_worktree_identity)
    try:
        # All protected Git reads are anchored to the already-open authorized
        # directory inode, so rename/swap races cannot redirect the evidence.
        branch, head, origin = require_clean_root(root, worktree_fd)
    finally:
        os.close(worktree_fd)
    # Require the authorized lexical path to point at the same captured inode
    # chain at the PASS boundary as well.
    verify_worktree_identity(root, args.expected_worktree_identity)
    origin_host, origin_repo = normalize_origin(origin)
    if origin_host != args.expected_origin_host.lower():
        raise PreflightError("ORIGIN_HOST_MISMATCH")
    if origin_repo != args.expected_repo:
        raise PreflightError("TARGET_REPO_MISMATCH")
    if branch != args.expected_branch:
        raise PreflightError("BRANCH_MISMATCH")
    if head != args.expected_head:
        raise PreflightError("HEAD_MISMATCH")

    print("IMPLEMENTATION_LOCAL_BINDING=PASS")
    print("PREFLIGHT_ARTIFACT_AUTHORITY=EXTERNAL_IMMUTABLE_SOURCE_REQUIRED")
    print("MUTATION_AUTHORITY=NO")
    print("AUTHORITY_BOUNDARY=EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED")
    print(f"TARGET_REPO={origin_repo}")
    print(f"WORKTREE={root}")
    print(f"WORKSTREAM={args.expected_workstream}")
    print(f"BRANCH={branch}")
    print(f"HEAD={head}")
    print(f"INTENT_REVISION={args.expected_intent_revision}")
    print(f"CHANGE_RISK={args.expected_change_risk}")
    print(f"IMPLEMENTER={DIRECT_CHAT_IMPLEMENTER}")
    print(f"PACKET_ISSUE={args.issue_number}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    identity = sub.add_parser("identity")
    identity.add_argument("--root", required=True)

    command = sub.add_parser("check")
    command.add_argument("--root", required=True)
    command.add_argument("--expected-worktree", required=True)
    command.add_argument("--expected-worktree-identity", required=True)
    command.add_argument("--issue-number", required=True, type=int)
    command.add_argument("--expected-repo", required=True)
    command.add_argument("--expected-origin-host", default="github.com")
    command.add_argument("--expected-workstream", required=True)
    command.add_argument("--expected-branch", required=True)
    command.add_argument("--expected-head", required=True)
    command.add_argument("--expected-intent-revision", required=True, type=int)
    command.add_argument(
        "--expected-change-risk",
        required=True,
        choices=sorted(CHANGE_RISKS),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "identity":
            root = _lexical_absolute_path(args.root, reason="ROOT_PATH_INVALID")
            print(f"WORKTREE_IDENTITY={worktree_identity(root)}")
            print("MUTATION_AUTHORITY=NO")
            return 0
        if args.command == "check":
            return check(args)
        raise PreflightError("COMMAND_INVALID")
    except PreflightError as exc:
        fail(str(exc))
    return 2


if __name__ == "__main__":
    # The repository/worktree copy is worker-writable and therefore can never be
    # mutation authority. Authoritative execution uses immutable source bytes
    # supplied on stdin (Python sets __file__ to "<stdin>") through an isolated
    # trusted interpreter controlled by the external coordinator.
    if globals().get("__file__") != "<stdin>":
        fail("REFERENCE_ONLY_ARTIFACT")
    raise SystemExit(main())
