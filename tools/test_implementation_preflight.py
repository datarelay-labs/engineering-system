#!/usr/bin/env python3
"""Regression tests for Chat-primary local Git binding evidence."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

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
FIXTURE_GIT_PATH = next(
    (
        candidate
        for candidate in PREFLIGHT.TRUSTED_GIT_CANDIDATES
        if candidate.is_file() and os.access(candidate, os.X_OK)
    ),
    None,
)


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        input=input_text,
    )
    if check and result.returncode:
        raise AssertionError(result.stdout)
    return result


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # Fixture setup is intentionally independent from the operational trust
    # decision. This lets root-owned CI/container environments verify that the
    # real preflight blocks root workers instead of making the regression suite
    # itself impossible to run.
    assert FIXTURE_GIT_PATH is not None
    return run(str(FIXTURE_GIT_PATH), *args, cwd=cwd)


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


def fixture_worktree_identity(path: Path) -> str:
    records: list[str] = []
    current = Path(path.parts[0])
    for part in path.parts[1:]:
        current = current / part
        st = os.lstat(current)
        if os.path.islink(current):
            raise AssertionError(f"fixture path unexpectedly symlinked: {current}")
        records.append(f"{st.st_dev}:{st.st_ino}")
    root_st = os.lstat(Path(path.parts[0]))
    return ",".join([f"{root_st.st_dev}:{root_st.st_ino}", *records])


def isolated_python_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
    }


def invoke(
    repo: Path,
    head: str,
    *extra: str,
    env: dict[str, str] | None = None,
    tool: Path = TOOL,
    expected_identity: str | None = None,
) -> subprocess.CompletedProcess[str]:
    # Match the authoritative execution shape: immutable source bytes on stdin,
    # Python isolated mode, cwd outside the worktree, and no inherited caller
    # environment. env is accepted only so adversarial callers can prove their
    # supplied values are ignored by this launcher.
    _ = env
    args = [
        sys.executable,
        "-I",
        "-",
        "check",
        "--root",
        str(repo),
        "--expected-worktree",
        str(repo),
        "--expected-worktree-identity",
        expected_identity or fixture_worktree_identity(repo),
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
    return run(
        *args,
        cwd=Path("/"),
        env=isolated_python_env(),
        check=False,
        input_text=tool.read_text(encoding="utf-8"),
    )


def main() -> int:
    if FIXTURE_GIT_PATH is None:
        raise SystemExit("IMPLEMENTATION_PREFLIGHT_TESTS=FAIL fixture Git unavailable")

    running_as_root = os.geteuid() == 0
    if running_as_root:
        assert GIT_PATH is None
    else:
        assert GIT_PATH is not None
        assert PREFLIGHT._root_administered_path(GIT_PATH, executable=True)
    with mock.patch.object(PREFLIGHT.os, "geteuid", return_value=0):
        if GIT_PATH is not None:
            assert not PREFLIGHT._root_administered_path(GIT_PATH, executable=True)
        assert PREFLIGHT.resolve_trusted_git() is None

    # Python import isolation is part of the authority boundary. Worker-owned
    # cwd modules, PYTHONPATH, and user-site customization must not run before
    # the immutable stdin helper.
    with tempfile.TemporaryDirectory() as poison_tmp:
        poison = Path(poison_tmp)
        (poison / "argparse.py").write_text(
            'print("FORGED_ARGPARSE_IMPORT")\nraise SystemExit(0)\n',
            encoding="utf-8",
        )
        userbase = poison / "userbase"
        site = userbase / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        site.mkdir(parents=True)
        (site / "usercustomize.py").write_text(
            'print("FORGED_USERCUSTOMIZE")\n',
            encoding="utf-8",
        )
        poisoned_env = {
            **isolated_python_env(),
            "PYTHONPATH": str(poison),
            "PYTHONUSERBASE": str(userbase),
        }
        isolated_help = run(
            sys.executable,
            "-I",
            "-",
            "check",
            "--help",
            cwd=poison,
            env=poisoned_env,
            check=False,
            input_text=TOOL.read_text(encoding="utf-8"),
        )
        assert isolated_help.returncode == 0, isolated_help.stdout
        assert "--expected-worktree" in isolated_help.stdout
        assert "FORGED_ARGPARSE_IMPORT" not in isolated_help.stdout
        assert "FORGED_USERCUSTOMIZE" not in isolated_help.stdout

    # The worker-writable repository copy is reference/test material only.
    direct = run(
        sys.executable,
        str(TOOL),
        "check",
        "--help",
        cwd=ROOT,
        check=False,
    )
    assert direct.returncode == 2, direct.stdout
    assert "REFERENCE_ONLY_ARTIFACT" in direct.stdout
    assert "IMPLEMENTATION_LOCAL_BINDING=PASS" not in direct.stdout
    assert "MUTATION_AUTHORITY=NO" in direct.stdout

    env = isolated_python_env()
    assert not any(name.startswith("PYTHON") for name in env)
    assert env["HOME"] == "/nonexistent"
    assert env["XDG_CONFIG_HOME"] == "/nonexistent"

    with tempfile.TemporaryDirectory() as identity_tmp:
        identity_root = Path(identity_tmp) / "identity-repo"
        identity_root.mkdir()
        identity_result = run(
            sys.executable,
            "-I",
            "-",
            "identity",
            "--root",
            str(identity_root),
            cwd=Path("/"),
            env=isolated_python_env(),
            check=False,
            input_text=TOOL.read_text(encoding="utf-8"),
        )
        assert identity_result.returncode == 0, identity_result.stdout
        assert f"WORKTREE_IDENTITY={fixture_worktree_identity(identity_root)}" in identity_result.stdout
        assert "MUTATION_AUTHORITY=NO" in identity_result.stdout

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        head = init_repo(repo)

        if running_as_root:
            blocked_root = invoke(repo, head)
            assert blocked_root.returncode == 2, blocked_root.stdout
            assert "LOCAL_GIT_BOUNDARY_UNAVAILABLE" in blocked_root.stdout
            assert "MUTATION_AUTHORITY=NO" in blocked_root.stdout
            print("IMPLEMENTATION_PREFLIGHT_TESTS=PASS ROOT_OPERATIONAL_BLOCK=VERIFIED")
            return 0

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
            "IMPLEMENTATION_RUNTIME_AUTHORITY=EXTERNAL_EXECUTION_PROFILE",
            "PACKET_ISSUE=143",
        ):
            assert token in passed.stdout, token

        help_result = run(
            sys.executable,
            "-I",
            "-",
            "check",
            "--help",
            cwd=Path("/"),
            env=isolated_python_env(),
            input_text=TOOL.read_text(encoding="utf-8"),
        )
        assert help_result.returncode == 0
        for required in (
            "--expected-worktree",
            "--expected-worktree-identity",
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

        # Coordinator-captured identity must reject a final-directory replacement
        # even when the replacement clone has the same origin/branch/HEAD.
        with tempfile.TemporaryDirectory() as swap_tmp:
            swap_root = Path(swap_tmp)
            original = swap_root / "repo"
            original.mkdir()
            swap_head = init_repo(original)
            captured = fixture_worktree_identity(original)
            hidden = swap_root / "repo-hidden"
            original.rename(hidden)
            git("clone", "-q", str(hidden), str(original), cwd=swap_root)
            git(
                "remote",
                "set-url",
                "origin",
                "https://github.com/datarelay-labs/engineering-system.git",
                cwd=original,
            )
            replaced = invoke(
                original,
                swap_head,
                expected_identity=captured,
            )
            assert replaced.returncode == 2, replaced.stdout
            assert "WORKTREE_IDENTITY_MISMATCH" in replaced.stdout

        # A symlink in any lexical ancestor is forbidden even when it still
        # resolves to the originally authorized repository inode.
        with tempfile.TemporaryDirectory() as ancestor_tmp:
            ancestor_root = Path(ancestor_tmp)
            parent = ancestor_root / "parent"
            parent.mkdir()
            ancestor_repo = parent / "repo"
            ancestor_repo.mkdir()
            ancestor_head = init_repo(ancestor_repo)
            captured = fixture_worktree_identity(ancestor_repo)
            hidden_parent = ancestor_root / "parent-hidden"
            parent.rename(hidden_parent)
            parent.symlink_to(hidden_parent, target_is_directory=True)
            symlinked = invoke(
                ancestor_repo,
                ancestor_head,
                expected_identity=captured,
            )
            assert symlinked.returncode == 2, symlinked.stdout
            assert "WORKTREE_PATH_SYMLINK" in symlinked.stdout

        # Protected Git reads must stay anchored to the originally opened inode
        # even if the lexical worktree path is replaced while the check runs.
        with tempfile.TemporaryDirectory() as fd_swap_tmp:
            fd_swap_root = Path(fd_swap_tmp)
            fd_original = fd_swap_root / "repo"
            fd_original.mkdir()
            fd_head = init_repo(fd_original)
            fd_captured = fixture_worktree_identity(fd_original)
            bound_fd = PREFLIGHT.open_bound_worktree(fd_original, fd_captured)
            try:
                (fd_original / "README.md").write_text("dirty-through-original-inode\n", encoding="utf-8")
                fd_hidden = fd_swap_root / "repo-hidden"
                fd_original.rename(fd_hidden)
                git("clone", "-q", str(fd_hidden), str(fd_original), cwd=fd_swap_root)
                git(
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/datarelay-labs/engineering-system.git",
                    cwd=fd_original,
                )
                try:
                    PREFLIGHT.require_clean_root(fd_original, bound_fd)
                except PREFLIGHT.PreflightError as exc:
                    assert str(exc) == "WORKTREE_DIRTY", exc
                else:
                    raise AssertionError("fd-bound Git reads followed replacement worktree")
            finally:
                os.close(bound_fd)

        # A worker-controlled local core.worktree must not redirect protected
        # Git reads away from the fd-bound authorized worktree.
        with tempfile.TemporaryDirectory() as core_worktree_tmp:
            clean_sibling = Path(core_worktree_tmp) / "clean"
            git("clone", "-q", str(repo), str(clean_sibling), cwd=root)
            (repo / "README.md").write_text("dirty-local-worktree\n", encoding="utf-8")
            git("config", "core.worktree", str(clean_sibling), cwd=repo)
            redirected = invoke(repo, head)
            assert redirected.returncode == 2, redirected.stdout
            assert "WORKTREE_DIRTY" in redirected.stdout
            git("config", "--unset", "core.worktree", cwd=repo)
            (repo / "README.md").write_text("base\n", encoding="utf-8")

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

        # Preserve legitimate trailing spaces in Git path output. A submodule
        # whose path ends in a space must still be recursively inspected for
        # hidden index state instead of being silently skipped.
        spaced_child = root / "spaced-submodule-source"
        spaced_child.mkdir()
        git("init", "-q", "-b", "main", str(spaced_child))
        git("config", "user.email", "test@example.invalid", cwd=spaced_child)
        git("config", "user.name", "Preflight Test", cwd=spaced_child)
        (spaced_child / "spaced.txt").write_text("base\n", encoding="utf-8")
        git("add", ".", cwd=spaced_child)
        git("commit", "-qm", "spaced child base", cwd=spaced_child)
        spaced_rel = "deps/fixture-space "
        git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(spaced_child),
            spaced_rel,
            cwd=repo,
        )
        git("commit", "-qam", "add trailing-space submodule", cwd=repo)
        head = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        spaced_submodule = repo / spaced_rel
        git("update-index", "--assume-unchanged", "spaced.txt", cwd=spaced_submodule)
        spaced_hidden = invoke(repo, head)
        assert spaced_hidden.returncode == 2, spaced_hidden.stdout
        assert "HIDDEN_INDEX_STATE" in spaced_hidden.stdout
        git("update-index", "--no-assume-unchanged", "spaced.txt", cwd=spaced_submodule)
        spaced_clean = invoke(repo, head)
        assert spaced_clean.returncode == 0, spaced_clean.stdout

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
    assert "not self-authenticating" in source
    assert "EXTERNAL_IMMUTABLE_SOURCE_REQUIRED" in source
    assert "WORKTREE_IDENTITY_MISMATCH" in source
    assert "WORKTREE_PATH_SYMLINK" in source
    assert "--expected-worktree-identity" in source

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
        'IMPLEMENTATION_RUNTIME_AUTHORITY=EXTERNAL_EXECUTION_PROFILE',
        'AUTHORITY_BOUNDARY = "EXTERNAL_AUTHENTICATED_GITHUB_COORDINATOR_REQUIRED"',
        'Path("/usr/bin/git")',
        "st.st_uid != 0",
        "os.geteuid() == 0",
        "MUTATION_AUTHORITY=NO",
        "GIT_CONFIG_NOSYSTEM",
        "core.fsmonitor=false",
        "HIDDEN_INDEX_STATE",
        '"ls-files", "-v", "-z"',
        '"ls-files", "--stage", "-z"',
        "open_bound_worktree",
        "pass_fds=(worktree_fd,)",
        "WORKTREE_FD_BOUNDARY_UNAVAILABLE",
    ):
        assert required in source, required

    print("IMPLEMENTATION_PREFLIGHT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
