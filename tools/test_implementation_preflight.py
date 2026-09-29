#!/usr/bin/env python3
"""Regression tests for Chat-primary local Git binding evidence."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "implementation_preflight.py"


def load_preflight():
    spec = importlib.util.spec_from_file_location("implementation_preflight_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT = load_preflight()
GIT_PATH = PREFLIGHT.resolve_trusted_git()


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
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


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    assert GIT_PATH is not None
    return run(str(GIT_PATH), *args, cwd=cwd)


def init_repo(root: Path) -> str:
    git("init", "-q", "-b", "feat/chat-primary", str(root))
    git("config", "user.email", "test@example.invalid", cwd=root)
    git("config", "user.name", "Preflight Test", cwd=root)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git("add", ".", cwd=root)
    git("commit", "-qm", "fixture", cwd=root)
    git(
        "remote",
        "add",
        "origin",
        "https://github.com/datarelay-labs/engineering-system.git",
        cwd=root,
    )
    return git("rev-parse", "HEAD", cwd=root).stdout.strip()


def invoke(
    repo: Path,
    head: str,
    *extra: str,
    env: dict[str, str] | None = None,
    tool: Path = TOOL,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(tool),
        "check",
        "--root",
        str(repo),
        "--expected-worktree",
        str(repo),
        "--issue-number",
        "143",
        "--expected-repo",
        "datarelay-labs/engineering-system",
        "--expected-origin-host",
        "github.com",
        "--expected-workstream",
        "chat-primary-ssh-development-workflow",
        "--expected-branch",
        "feat/chat-primary",
        "--expected-head",
        head,
        "--expected-intent-revision",
        "3",
        "--expected-change-risk",
        "HIGH",
    ]
    args.extend(extra)
    return run(*args, cwd=repo, env=env, check=False)


def main() -> int:
    if GIT_PATH is None:
        raise SystemExit("IMPLEMENTATION_PREFLIGHT_TESTS=FAIL trusted Git unavailable")
    assert PREFLIGHT._root_administered_path(GIT_PATH, executable=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        head = init_repo(repo)

        passed = invoke(repo, head)
        assert passed.returncode == 0, passed.stdout
        for token in (
            "IMPLEMENTATION_LOCAL_BINDING=PASS",
            "MUTATION_AUTHORITY=NO",
            "AUTHORITY_BOUNDARY=EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED",
            "TARGET_REPO=datarelay-labs/engineering-system",
            "WORKSTREAM=chat-primary-ssh-development-workflow",
            "BRANCH=feat/chat-primary",
            f"HEAD={head}",
            "INTENT_REVISION=3",
            "CHANGE_RISK=HIGH",
            "IMPLEMENTER=CHATGPT_CHAT",
            "PACKET_ISSUE=143",
        ):
            assert token in passed.stdout, token

        help_result = run(sys.executable, str(TOOL), "check", "--help")
        assert help_result.returncode == 0
        for required in (
            "--expected-worktree",
            "--issue-number",
            "--expected-repo",
            "--expected-workstream",
            "--expected-branch",
            "--expected-head",
            "--expected-intent-revision",
            "--expected-change-risk",
        ):
            assert required in help_result.stdout
        for forbidden in (
            "--packet-body-file",
            "--expected-packet-body-sha256",
            "--author-permission",
            "--connector-attested-author-permission",
            "--expected-implementer",
            "--github-token",
            "--gh-path",
        ):
            assert forbidden not in help_result.stdout

        cases = [
            (
                "worktree",
                ["--expected-worktree", str(root / "other")],
                "WORKTREE_BINDING_MISMATCH",
            ),
            ("repo", ["--expected-repo", "evil/repo"], "TARGET_REPO_MISMATCH"),
            ("branch", ["--expected-branch", "feat/other"], "BRANCH_MISMATCH"),
            ("head", ["--expected-head", "f" * 40], "HEAD_MISMATCH"),
        ]
        for name, extra, reason in cases:
            result = invoke(repo, head, *extra)
            assert result.returncode == 2, (name, result.stdout)
            assert reason in result.stdout, (name, result.stdout)
            assert "MUTATION_AUTHORITY=NO" in result.stdout

        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = invoke(repo, head)
        assert dirty.returncode == 2 and "WORKTREE_DIRTY" in dirty.stdout
        (repo / "dirty.txt").unlink()

        git("update-index", "--assume-unchanged", "README.md", cwd=repo)
        hidden_assume = invoke(repo, head)
        assert hidden_assume.returncode == 2, hidden_assume.stdout
        assert "HIDDEN_INDEX_STATE" in hidden_assume.stdout
        git("update-index", "--no-assume-unchanged", "README.md", cwd=repo)

        git("update-index", "--skip-worktree", "README.md", cwd=repo)
        hidden_skip = invoke(repo, head)
        assert hidden_skip.returncode == 2, hidden_skip.stdout
        assert "HIDDEN_INDEX_STATE" in hidden_skip.stdout
        git("update-index", "--no-skip-worktree", "README.md", cwd=repo)

        # Submodule state is part of the clean-tree authority boundary even if
        # repository config tries to suppress it.
        child = root / "submodule-source"
        child.mkdir()
        git("init", "-q", "-b", "main", str(child))
        git("config", "user.email", "test@example.invalid", cwd=child)
        git("config", "user.name", "Preflight Test", cwd=child)
        (child / "child.txt").write_text("base\n", encoding="utf-8")
        git("add", ".", cwd=child)
        git("commit", "-qm", "child base", cwd=child)

        git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(child),
            "deps/fixture",
            cwd=repo,
        )
        git("commit", "-qam", "add submodule", cwd=repo)
        head = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        git("config", "submodule.deps/fixture.ignore", "all", cwd=repo)

        clean_submodule = invoke(repo, head)
        assert clean_submodule.returncode == 0, clean_submodule.stdout

        submodule = repo / "deps" / "fixture"
        git("config", "user.email", "test@example.invalid", cwd=submodule)
        git("config", "user.name", "Preflight Test", cwd=submodule)

        git("update-index", "--assume-unchanged", "child.txt", cwd=submodule)
        nested_hidden_assume = invoke(repo, head)
        assert nested_hidden_assume.returncode == 2, nested_hidden_assume.stdout
        assert "HIDDEN_INDEX_STATE" in nested_hidden_assume.stdout
        git("update-index", "--no-assume-unchanged", "child.txt", cwd=submodule)

        git("update-index", "--skip-worktree", "child.txt", cwd=submodule)
        nested_hidden_skip = invoke(repo, head)
        assert nested_hidden_skip.returncode == 2, nested_hidden_skip.stdout
        assert "HIDDEN_INDEX_STATE" in nested_hidden_skip.stdout
        git("update-index", "--no-skip-worktree", "child.txt", cwd=submodule)

        (submodule / "child.txt").write_text("dirty\n", encoding="utf-8")
        dirty_submodule = invoke(repo, head)
        assert dirty_submodule.returncode == 2, dirty_submodule.stdout
        assert "WORKTREE_DIRTY" in dirty_submodule.stdout
        git("checkout", "--", "child.txt", cwd=submodule)

        (submodule / "child.txt").write_text("next\n", encoding="utf-8")
        git("add", "child.txt", cwd=submodule)
        git("commit", "-qm", "child next", cwd=submodule)
        mismatched_gitlink = invoke(repo, head)
        assert mismatched_gitlink.returncode == 2, mismatched_gitlink.stdout
        assert "WORKTREE_DIRTY" in mismatched_gitlink.stdout
        git("reset", "--hard", "HEAD^", cwd=submodule)

        clean_again = invoke(repo, head)
        assert clean_again.returncode == 0, clean_again.stdout

        git(
            "remote",
            "set-url",
            "origin",
            "https://evil.invalid/datarelay-labs/engineering-system.git",
            cwd=repo,
        )
        wrong_origin = invoke(repo, head)
        assert wrong_origin.returncode == 2
        assert "ORIGIN_HOST_MISMATCH" in wrong_origin.stdout
        git(
            "remote",
            "set-url",
            "origin",
            "https://github.com/datarelay-labs/engineering-system.git",
            cwd=repo,
        )

        # Caller PATH/HOME/GIT_* values cannot replace the host Git boundary.
        attacker_bin = root / "attacker-bin"
        attacker_bin.mkdir()
        fake_git = attacker_bin / "git"
        fake_git.write_text("#!/bin/sh\nprintf 'forged-by-path\\n'\nexit 0\n", encoding="utf-8")
        fake_git.chmod(0o755)
        assert not PREFLIGHT._root_administered_path(fake_git, executable=True)
        symlink_git = root / "git-link"
        symlink_git.symlink_to(GIT_PATH)
        assert not PREFLIGHT._root_administered_path(symlink_git, executable=True)

        poisoned = dict(os.environ)
        poisoned["PATH"] = str(attacker_bin) + os.pathsep + poisoned.get("PATH", "")
        poisoned["HOME"] = str(root / "attacker-home")
        poisoned["GIT_CONFIG_GLOBAL"] = str(root / "attacker.gitconfig")
        poisoned["GIT_DIR"] = str(root / "attacker-git-dir")
        (root / "attacker.gitconfig").write_text(
            "[core]\n    fsmonitor = /definitely/not/trusted\n",
            encoding="utf-8",
        )
        path_attack = invoke(repo, head, env=poisoned)
        assert path_attack.returncode == 0, path_attack.stdout
        assert f"HEAD={head}" in path_attack.stdout
        assert "forged-by-path" not in path_attack.stdout

        # Repository-local executable config must not run before local binding.
        sentinel = root / "fsmonitor-ran"
        hook = root / "fsmonitor-hook"
        hook.write_text(
            "#!/bin/sh\nprintf ran > " + str(sentinel) + "\nexit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        git("config", "core.fsmonitor", str(hook), cwd=repo)
        fsmonitor = invoke(repo, head)
        assert fsmonitor.returncode == 0, fsmonitor.stdout
        assert not sentinel.exists()

        env = PREFLIGHT._bounded_git_env()
        assert env["HOME"] == "/nonexistent"
        assert env["XDG_CONFIG_HOME"] == "/nonexistent"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        source = TOOL.read_text(encoding="utf-8")
        assert "core.fsmonitor=false" in source
        assert "core.hooksPath=/dev/null" in source
        assert "credential.helper=" in source
        assert "--ignore-submodules=none" in source
        assert "--ignore-submodules=all" not in source
        assert '"--no-includes"' in source

    source = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "gh api",
        "TRUSTED_GH",
        "authenticated_packet",
        "collaborators/{author}/permission",
        "AUTHOR_PERMISSION=",
        "PACKET_BODY_SHA256=",
        "context_epoch",
        "work_packet_authority",
        "Path.home()",
        "shutil.which",
    ):
        assert forbidden not in source, forbidden
    for required in (
        'DIRECT_CHAT_IMPLEMENTER = "CHATGPT_CHAT"',
        'AUTHORITY_BOUNDARY = "EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED"',
        'Path("/usr/bin/git")',
        "st.st_uid != 0",
        "MUTATION_AUTHORITY=NO",
        "GIT_CONFIG_NOSYSTEM",
        "core.fsmonitor=false",
        "HIDDEN_INDEX_STATE",
        'git(root, "ls-files", "-v", "-z")',
        'git(root, "ls-files", "--stage", "-z")',
    ):
        assert required in source, required

    print("IMPLEMENTATION_PREFLIGHT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
