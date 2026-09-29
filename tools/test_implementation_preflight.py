#!/usr/bin/env python3
"""Regression tests for Chat-primary local implementation binding evidence."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "implementation_preflight.py"
GIT = "/usr/bin/git"


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and result.returncode:
        raise AssertionError(result.stdout)
    return result


def init_repo(root: Path) -> str:
    run(GIT, "init", "-q", "-b", "feat/chat-primary", str(root))
    run(GIT, "config", "user.email", "test@example.invalid", cwd=root)
    run(GIT, "config", "user.name", "Preflight Test", cwd=root)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    run(GIT, "add", ".", cwd=root)
    run(GIT, "commit", "-qm", "fixture", cwd=root)
    run(GIT, "remote", "add", "origin", "https://github.com/datarelay-labs/engineering-system.git", cwd=root)
    return run(GIT, "rev-parse", "HEAD", cwd=root).stdout.strip()


def invoke(tool: Path, repo: Path, head: str, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable, str(tool), "check",
        "--root", str(repo),
        "--expected-worktree", str(repo),
        "--issue-number", "146",
        "--expected-repo", "datarelay-labs/engineering-system",
        "--expected-origin-host", "github.com",
        "--expected-workstream", "chat-primary-trust-boundary-hardening",
        "--expected-branch", "feat/chat-primary",
        "--expected-head", head,
        "--expected-intent-revision", "1",
        "--expected-change-risk", "HIGH",
    ]
    args.extend(extra)
    return run(*args, cwd=repo, env=env, check=False)


def main() -> int:
    if not Path(GIT).is_file():
        raise SystemExit("IMPLEMENTATION_PREFLIGHT_TESTS=FAIL missing /usr/bin/git")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        head = init_repo(repo)

        passed = invoke(TOOL, repo, head)
        assert passed.returncode == 0, passed.stdout
        for token in (
            "IMPLEMENTATION_LOCAL_BINDING=PASS",
            "MUTATION_AUTHORITY=NO",
            "AUTHORITY_BOUNDARY=EXTERNAL_COORDINATOR_REQUIRED",
            "TARGET_REPO=datarelay-labs/engineering-system",
            "WORKSTREAM=chat-primary-trust-boundary-hardening",
            "BRANCH=feat/chat-primary",
            f"HEAD={head}",
            "INTENT_REVISION=1",
            "CHANGE_RISK=HIGH",
            "IMPLEMENTER=CHATGPT_CHAT",
            "PACKET_ISSUE=146",
        ):
            assert token in passed.stdout, token
        assert "AUTHOR_PERMISSION=" not in passed.stdout
        assert "PACKET_BODY_SHA256=" not in passed.stdout

        help_result = run(sys.executable, str(TOOL), "check", "--help")
        for required in (
            "--expected-worktree", "--issue-number", "--expected-repo",
            "--expected-workstream", "--expected-branch", "--expected-head",
            "--expected-intent-revision", "--expected-change-risk",
        ):
            assert required in help_result.stdout
        for forbidden in (
            "--packet-body-file", "--expected-packet-body-sha256",
            "--author-permission", "--expected-implementer",
            "--github-token", "--gh-path",
        ):
            assert forbidden not in help_result.stdout

        cases = [
            ("worktree", ["--expected-worktree", str(root / "other")], "WORKTREE_BINDING_MISMATCH"),
            ("repo", ["--expected-repo", "evil/repo"], "TARGET_REPO_MISMATCH"),
            ("branch", ["--expected-branch", "feat/other"], "BRANCH_MISMATCH"),
            ("head", ["--expected-head", "f" * 40], "HEAD_MISMATCH"),
        ]
        for name, extra, reason in cases:
            result = invoke(TOOL, repo, head, *extra)
            assert result.returncode == 2, (name, result.stdout)
            assert reason in result.stdout, (name, result.stdout)
            assert "MUTATION_AUTHORITY=NO" in result.stdout

        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = invoke(TOOL, repo, head)
        assert dirty.returncode == 2 and "WORKTREE_DIRTY" in dirty.stdout
        (repo / "dirty.txt").unlink()

        run(GIT, "remote", "set-url", "origin", "https://evil.invalid/datarelay-labs/engineering-system.git", cwd=repo)
        wrong_origin = invoke(TOOL, repo, head)
        assert wrong_origin.returncode == 2
        assert "ORIGIN_HOST_MISMATCH" in wrong_origin.stdout
        run(GIT, "remote", "set-url", "origin", "https://github.com/datarelay-labs/engineering-system.git", cwd=repo)

        attacker_bin = root / "attacker-bin"
        attacker_bin.mkdir()
        fake_git = attacker_bin / "git"
        fake_git.write_text("#!/bin/sh\nprintf '%s\\n' 'forged-by-path'\nexit 0\n", encoding="utf-8")
        fake_git.chmod(0o755)
        poisoned = dict(os.environ)
        poisoned["PATH"] = str(attacker_bin) + os.pathsep + poisoned.get("PATH", "")
        poisoned["HOME"] = str(root / "attacker-home")
        poisoned["GIT_CONFIG_GLOBAL"] = str(root / "attacker.gitconfig")
        (root / "attacker.gitconfig").write_text("[url \"https://evil.invalid/\"]\n    insteadOf = https://github.com/\n", encoding="utf-8")
        path_attack = invoke(TOOL, repo, head, env=poisoned)
        assert path_attack.returncode == 0, path_attack.stdout
        assert f"HEAD={head}" in path_attack.stdout
        assert "forged-by-path" not in path_attack.stdout

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "adopted"
        repo.mkdir()
        init_repo(repo)
        managed = repo / "tools"
        managed.mkdir()
        shutil.copy2(TOOL, managed / "implementation_preflight.py")
        run(GIT, "add", "tools", cwd=repo)
        run(GIT, "commit", "-qm", "managed implementation preflight", cwd=repo)
        head = run(GIT, "rev-parse", "HEAD", cwd=repo).stdout.strip()
        result = invoke(managed / "implementation_preflight.py", repo, head)
        assert result.returncode == 0, result.stdout
        assert "IMPLEMENTATION_LOCAL_BINDING=PASS" in result.stdout
        assert "MUTATION_AUTHORITY=NO" in result.stdout
        assert run(GIT, "status", "--porcelain", cwd=repo).stdout == ""

    source = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "gh api", "TRUSTED_GH", "authenticated_packet",
        "collaborators/{author}/permission", "AUTHOR_PERMISSION=",
        "PACKET_BODY_SHA256=", "Path.home()", "shutil.which",
    ):
        assert forbidden not in source, forbidden
    for required in (
        'DIRECT_CHAT_IMPLEMENTER = "CHATGPT_CHAT"',
        'Path("/usr/bin/git")',
        "st_uid != 0",
        "MUTATION_AUTHORITY=NO",
        "AUTHORITY_BOUNDARY=EXTERNAL_COORDINATOR_REQUIRED",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
    ):
        assert required in source, required

    print("IMPLEMENTATION_PREFLIGHT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
