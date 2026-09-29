#!/usr/bin/env python3
"""Regression tests for provider-neutral implementation preflight."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "implementation_preflight.py"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def load_preflight(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT = load_preflight(TOOL, "implementation_preflight_under_test")


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def init_repo(root: Path) -> str:
    run("git", "init", "-q", "-b", "feat/chat-primary", str(root))
    run("git", "config", "user.email", "test@example.invalid", cwd=root)
    run("git", "config", "user.name", "Preflight Test", cwd=root)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-qm", "fixture", cwd=root)
    run(
        "git",
        "remote",
        "add",
        "origin",
        "git@github.com:datarelay-labs/engineering-system.git",
        cwd=root,
    )
    return run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()


def packet(head: str, **overrides: str) -> str:
    values = {
        "TARGET_REPO": "datarelay-labs/engineering-system",
        "WORKSTREAM": "chat-primary-ssh-development-workflow",
        "STATUS": "ACTIVE",
        "BRANCH": "feat/chat-primary",
        "LAST_VERIFIED_HEAD": head,
        "INTENT_REVISION": "2",
        "CHANGE_RISK": "HIGH",
        "IMPLEMENTER": "CHATGPT_CHAT",
    }
    values.update(overrides)
    return f"""PACKET_VERSION=2
TARGET_REPO={values['TARGET_REPO']}
WORKSTREAM={values['WORKSTREAM']}
STATUS={values['STATUS']}
BRANCH={values['BRANCH']}
TASK_KIND=IMPLEMENTATION_AND_TEST
OWNER_INTENT=Implement the authorized bounded change.
LAST_VERIFIED_HEAD={values['LAST_VERIFIED_HEAD']}
INTENT_REVISION={values['INTENT_REVISION']}
CHANGE_RISK={values['CHANGE_RISK']}
IMPLEMENTER={values['IMPLEMENTER']}

## Goal

Implement the authorized change.

## Current State

Clean exact-head worktree.

## Next Action

Implement and validate.

## Blockers

NONE
"""


def github_fixture(
    root: Path,
    body: Path,
    *,
    permission: str = "admin",
    state: str = "open",
    title: str = "[AI Work] Fixture",
    author: str = "fixture-user",
) -> Path:
    issue_path = root / "issue.json"
    permission_path = root / "permission.json"
    issue_path.write_text(
        json.dumps(
            {
                "number": 143,
                "state": state,
                "title": title,
                "body": body.read_text(encoding="utf-8"),
                "user": {"login": author},
            }
        ),
        encoding="utf-8",
    )
    permission_path.write_text(
        json.dumps({"permission": permission}),
        encoding="utf-8",
    )
    fake_bin = root / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_bin.chmod(0o755)
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"issue={str(issue_path)!r}\n"
        f"permission={str(permission_path)!r}\n"
        "endpoint=sys.argv[-1]\n"
        "path=permission if endpoint.endswith('/permission') else issue\n"
        "print(open(path, encoding='utf-8').read())\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return gh


def invoke(
    repo: Path,
    body: Path,
    head: str,
    *extra: str,
    permission: str = "admin",
    state: str = "open",
    title: str = "[AI Work] Fixture",
    module=PREFLIGHT,
) -> subprocess.CompletedProcess[str]:
    args = [
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
        "--expected-head",
        head,
        "--expected-intent-revision",
        "2",
        "--expected-implementer",
        "CHATGPT_CHAT",
        "--expected-change-risk",
        "HIGH",
    ]
    args.extend(extra)
    gh = github_fixture(
        body.parent,
        body,
        permission=permission,
        state=state,
        title=title,
    )
    module._TEST_TRUSTED_GH = gh
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                rc = module.main(args)
            except SystemExit as exc:
                rc = int(exc.code or 0)
    finally:
        module._TEST_TRUSTED_GH = None
    return subprocess.CompletedProcess(args=args, returncode=rc, stdout=out.getvalue())


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        head = init_repo(repo)
        body = root / "packet.md"
        body.write_text(packet(head), encoding="utf-8")

        passed = invoke(repo, body, head)
        assert passed.returncode == 0, passed.stdout
        assert "IMPLEMENTATION_PREFLIGHT=PASS" in passed.stdout
        assert f"HEAD={head}" in passed.stdout
        assert "IMPLEMENTER=CHATGPT_CHAT" in passed.stdout
        assert "AUTHOR_PERMISSION=admin" in passed.stdout
        assert "PACKET_BODY_SHA256=" in passed.stdout

        help_result = run(sys.executable, str(TOOL), "check", "--help")
        assert help_result.returncode == 0
        assert "--issue-number" in help_result.stdout
        for forbidden in (
            "--packet-body-file",
            "--expected-packet-body-sha256",
            "--author-permission",
        ):
            assert forbidden not in help_result.stdout

        weak_permission = invoke(repo, body, head, permission="read")
        assert weak_permission.returncode == 2
        assert "WORK_PACKET_AUTHOR_UNTRUSTED" in weak_permission.stdout

        closed_issue = invoke(repo, body, head, state="closed")
        assert closed_issue.returncode == 2
        assert "WORK_PACKET_NOT_ACTIVE" in closed_issue.stdout

        invalid_title = invoke(repo, body, head, title="Not a work packet")
        assert invalid_title.returncode == 2
        assert "WORK_PACKET_TITLE_INVALID" in invalid_title.stdout

        cases = [
            ("worktree", ["--expected-worktree", str(root / "other")], "WORKTREE_BINDING_MISMATCH"),
            ("intent", ["--expected-intent-revision", "3"], "STALE_INTENT_REVISION"),
            ("implementer", ["--expected-implementer", "CURSOR"], "IMPLEMENTER_MISMATCH"),
            ("risk", ["--expected-change-risk", "CRITICAL"], "CHANGE_RISK_MISMATCH"),
            ("repo", ["--expected-repo", "evil/repo"], "TARGET_REPO_MISMATCH"),
        ]
        for _name, extra, reason in cases:
            result = invoke(repo, body, head, *extra)
            assert result.returncode == 2, (_name, result.stdout)
            assert reason in result.stdout, (_name, result.stdout)

        body.write_text(packet(head, BRANCH="feat/other"), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "BRANCH_MISMATCH" in result.stdout

        body.write_text(packet("f" * 40), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "HEAD_MISMATCH" in result.stdout

        body.write_text(packet(head, STATUS="PAUSED"), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "WORK_PACKET_NOT_ACTIVE" in result.stdout

        body.write_text(packet(head, CHANGE_RISK="UNKNOWN"), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "CHANGE_RISK_INVALID" in result.stdout

        body.write_text(packet(head, IMPLEMENTER=""), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "IMPLEMENTER_INVALID" in result.stdout

        body.write_text(packet(head).replace("## Current State\n\nClean exact-head worktree.\n\n", ""), encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "MISSING_SECTION:Current State" in result.stdout

        body.write_text(packet(head), encoding="utf-8")
        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "WORKTREE_DIRTY" in result.stdout
        (repo / "dirty.txt").unlink()

        run("git", "remote", "set-url", "origin", "https://evil.invalid/datarelay-labs/engineering-system.git", cwd=repo)
        result = invoke(repo, body, head)
        assert result.returncode == 2 and "ORIGIN_HOST_MISMATCH" in result.stdout

    # Adopted repositories execute their managed copy from inside the target
    # worktree. The pre-mutation gate must not dirty that worktree merely by
    # importing its managed sibling helpers.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "adopted"
        repo.mkdir()
        head = init_repo(repo)
        managed = repo / "tools"
        managed.mkdir()
        for name in (
            "implementation_preflight.py",
            "context_epoch.py",
            "work_packet_authority.py",
        ):
            shutil.copy2(ROOT / "tools" / name, managed / name)
        run("git", "add", "tools", cwd=repo)
        run("git", "commit", "-qm", "managed preflight helpers", cwd=repo)
        head = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
        body = root / "packet.md"
        body.write_text(packet(head), encoding="utf-8")
        args = [
            sys.executable,
            str(managed / "implementation_preflight.py"),
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
            "--expected-head",
            head,
            "--expected-intent-revision",
            "2",
            "--expected-implementer",
            "CHATGPT_CHAT",
            "--expected-change-risk",
            "HIGH",
        ]
        managed_module = load_preflight(
            managed / "implementation_preflight.py",
            "managed_implementation_preflight_under_test",
        )
        result = invoke(
            repo,
            body,
            head,
            module=managed_module,
        )
        assert result.returncode == 0, result.stdout
        assert "IMPLEMENTATION_PREFLIGHT=PASS" in result.stdout
        assert not (managed / "__pycache__").exists()
        assert run("git", "status", "--porcelain", cwd=repo).stdout == ""

    print("IMPLEMENTATION_PREFLIGHT_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
