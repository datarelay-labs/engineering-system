#!/usr/bin/env python3
"""Deterministic intent replay and TEST/FIX/VERIFY phase checks.

A worker-writable checkout and self-authored plan cannot confer authority.
For hard enforcement use the OS-isolated TEST executor or an external,
trusted tool gateway; raw shell/MCP calls do NOT pass through this check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import resource
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

TEST_EFFECTS = frozenset({'read', 'test_run', 'evidence_write'})
FIX_EFFECTS = TEST_EFFECTS | frozenset({
    'source_write', 'test_contract_write', 'docs_write', 'git_commit',
    'git_push', 'candidate_build',
})
PHASE_EFFECTS = {'TEST': TEST_EFFECTS, 'FIX': FIX_EFFECTS, 'VERIFY': TEST_EFFECTS}
PROTECTED_EFFECTS = frozenset({
    'production_write', 'permissions_change', 'merge', 'release',
    'shared_destructive', 'credential_change',
})


class GuardError(ValueError):
    """Invalid or unenforceable phase boundary."""


def _result(status: str, reason: str, *, phase: str = '') -> dict[str, str]:
    return {'status': status, 'reason': reason, 'phase': phase,
            'mutation_authority': 'NO', 'enforcement': 'CALLER_SCOPED'}


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ['/usr/bin/git', '-c', 'safe.directory=' + str(root),
         '-c', 'core.fsmonitor=false', '-C', str(root), *args],
        text=True, capture_output=True, check=False, timeout=15,
        env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
    )
    if result.returncode:
        raise GuardError('GIT_INSPECTION_FAILED')
    return result.stdout.strip()


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HOST_RE = re.compile(r"^[a-zA-Z0-9.-]+$")
KNOWN_EFFECTS = TEST_EFFECTS | FIX_EFFECTS | PROTECTED_EFFECTS
OUTPUT_LIMIT_BYTES = 64 * 1024 * 1024  # bounded deterministic test output


def _origin_identity(raw_url: str) -> tuple[str, str] | None:
    """Identify a GitHub-style HTTPS, SSH URL or scp-style remote path."""
    if not isinstance(raw_url, str) or not raw_url:
        return None
    scp = re.fullmatch(r"(?:git@)([a-zA-Z0-9.-]+):(.+)", raw_url)
    if scp:
        host, remote_path = scp.group(1), scp.group(2)
    else:
        try:
            parsed = urlsplit(raw_url)
            if parsed.scheme not in ("https", "ssh", "git") or not parsed.hostname:
                return None
            host, remote_path = parsed.hostname, parsed.path.lstrip("/")
        except ValueError:
            return None
    if remote_path.endswith(".git"):
        remote_path = remote_path[:-4]
    if not REPO_RE.fullmatch(remote_path):
        return None
    return host.lower(), remote_path.lower()


def _check_repo_binding(root: Path, repo: str, host: str) -> None:
    try:
        remote = _git(root, 'config', '--local', '--get', 'remote.origin.url')
    except GuardError as exc:
        raise GuardError('REPOSITORY_ORIGIN_UNAVAILABLE') from exc
    identity = _origin_identity(remote)
    if identity != (host.lower(), repo.lower()):
        raise GuardError('REPOSITORY_ORIGIN_MISMATCH')


def _effect_set(value: object) -> frozenset[str]:
    if (not isinstance(value, list) or not value or
            any(not isinstance(e, str) or e not in KNOWN_EFFECTS for e in value) or
            len(value) != len(set(value))):
        raise GuardError('INTENT_EFFECTS_INVALID')
    return frozenset(value)


def _validate(plan: dict, effect: str) -> tuple[str, Path]:
    if not isinstance(plan, dict) or plan.get('contract_version') != 1:
        raise GuardError('CONTRACT_VERSION_INVALID')
    phase = plan.get('phase')
    if not isinstance(phase, str) or phase not in PHASE_EFFECTS:
        raise GuardError('PHASE_INVALID')
    if not isinstance(plan.get('agent_restatement'), dict):
        raise GuardError('INTENT_RESTATEMENT_MISSING')
    if plan.get('owner_phase') != phase or plan['agent_restatement'].get('phase') != phase:
        raise GuardError('INTENT_PHASE_MISMATCH')
    if not _nonempty(plan.get('repository')) or not REPO_RE.fullmatch(plan['repository']):
        raise GuardError('REPOSITORY_UNBOUND')
    git_host = plan.get('git_host', 'github.com')
    if not _nonempty(git_host) or not HOST_RE.fullmatch(git_host):
        raise GuardError('REPOSITORY_HOST_INVALID')
    intent = plan.get('owner_intent')
    replay = plan.get('agent_restatement')
    if not isinstance(intent, dict) or not isinstance(replay, dict) or not all(
        _nonempty(d.get(field)) for d in (intent, replay) for field in ('goal', 'problem')
    ) or not _nonempty(replay.get('next_phase')):
        raise GuardError('INTENT_RESTATEMENT_MISSING')
    # Effect-level differences are machine-checkable; free-form semantics are not.
    owner_effects = _effect_set(intent.get('allowed_effects'))
    replay_effects = _effect_set(replay.get('planned_effects'))
    if not replay_effects.issubset(owner_effects):
        raise GuardError('INTENT_EFFECT_MISMATCH')
    root_name = plan.get('worktree')
    if not _nonempty(root_name):
        raise GuardError('WORKTREE_UNBOUND')
    root = Path(root_name)
    if not root.is_absolute() or '..' in root.parts or root.is_symlink():
        raise GuardError('WORKTREE_PATH_INVALID')
    if not root.is_dir():
        raise GuardError('WORKTREE_UNAVAILABLE')
    _check_repo_binding(root, plan['repository'], git_host)
    contract_name = plan.get('source_contract')
    if not _nonempty(contract_name):
        raise GuardError('SOURCE_CONTRACT_UNBOUND')
    relative = Path(contract_name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise GuardError('SOURCE_CONTRACT_PATH_INVALID')
    contract = (root / relative).resolve()
    if not _inside(contract, root.resolve()) or not contract.is_file():
        raise GuardError('SOURCE_CONTRACT_UNAVAILABLE')
    digest = plan.get('source_contract_sha256')
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise GuardError('SOURCE_CONTRACT_DIGEST_INVALID')
    if _file_digest(contract) != digest:
        raise GuardError('CONTRACT_DRIFT')
    if not isinstance(effect, str) or effect not in KNOWN_EFFECTS:
        raise GuardError('EFFECT_UNKNOWN')
    return phase, root


def evaluate(plan: dict, effect: str) -> dict[str, str]:
    """Fail closed for an advertised effect. Does not execute or grant authority."""
    try:
        phase, root = _validate(plan, effect)
        if effect in PROTECTED_EFFECTS:
            return _result('BLOCK', 'EXTERNAL_TRUST_BOUNDARY_REQUIRED', phase=phase)
        if effect not in PHASE_EFFECTS[phase]:
            return _result('BLOCK', 'EFFECT_FORBIDDEN_IN_PHASE', phase=phase)
        if effect not in plan['owner_intent']['allowed_effects'] or effect not in plan['agent_restatement']['planned_effects']:
            return _result('BLOCK', 'INTENT_EFFECT_NOT_ALLOWED', phase=phase)
        if phase in ('TEST', 'VERIFY'):
            if _git(root, 'rev-parse', 'HEAD') != plan.get('pinned_head'):
                raise GuardError('HEAD_DRIFT')
            if _git(root, 'status', '--porcelain', '--untracked-files=all'):
                raise GuardError('SOURCE_DIRTY')
        else:
            frozen = plan.get('frozen_evidence')
            if not isinstance(frozen, dict) or not frozen.get('path') or not frozen.get('sha256'):
                raise GuardError('FROZEN_EVIDENCE_REQUIRED')
            evidence = Path(frozen['path'])
            if not evidence.is_absolute() or _inside(evidence.resolve(), root.resolve()):
                raise GuardError('FROZEN_EVIDENCE_PATH_INVALID')
            if not evidence.is_file() or _file_digest(evidence) != frozen['sha256']:
                raise GuardError('FROZEN_EVIDENCE_MISMATCH')
        return _result('ALLOW', 'EFFECT_ALLOWED_IN_PHASE_ONLY', phase=phase)
    except (GuardError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, GuardError) else 'GUARD_INSPECTION_FAILED'
        return _result('BLOCK', reason, phase=str(plan.get('phase', '')) if isinstance(plan, dict) else '')


def replay(plan: dict) -> dict:
    """Make the agent's interpretation visible before execution, never self-attest semantics."""
    if not isinstance(plan, dict):
        raise GuardError('INVALID_PLAN')
    result = evaluate(plan, 'read')
    return {
        'status': result['status'], 'reason': result['reason'],
        'owner_intent': plan.get('owner_intent'),
        'agent_restatement': plan.get('agent_restatement'),
        'phase': plan.get('phase'),
        'allowed_effects': sorted(
            PHASE_EFFECTS[plan['phase']] & frozenset(plan['owner_intent']['allowed_effects'])
            & frozenset(plan['agent_restatement']['planned_effects'])
        ) if result['status'] == 'ALLOW' else [],
        'denied_effects': sorted(
            KNOWN_EFFECTS - (PHASE_EFFECTS[plan['phase']]
            & frozenset(plan['owner_intent']['allowed_effects'])
            & frozenset(plan['agent_restatement']['planned_effects']))
        ) if result['status'] == 'ALLOW' else sorted(KNOWN_EFFECTS),
        'semantic_equivalence': 'NOT_MACHINE_VERIFIED',
        'authority': 'EXTERNAL_TOOL_GATEWAY_REQUIRED',
    }


def _write_permission(mode: int, uid: int, gid: int, groups: set[int], st: os.stat_result) -> bool:
    if uid == st.st_uid:
        return bool(mode & stat.S_IWUSR)
    if st.st_gid in groups | {gid}:
        return bool(mode & stat.S_IWGRP)
    return bool(mode & stat.S_IWOTH)


def _assert_source_read_only(root: Path, uid: int, gid: int, groups: set[int]) -> None:
    """Enforce OS mode ownership invariants before dropping privilege."""
    if root != root.resolve() or not (root / '.git').is_dir() or (root / '.git').is_symlink():
        # A Git linked worktree may have writable gitdir metadata outside this
        # checked-out tree; require a standalone sealed checkout instead.
        raise GuardError('STANDALONE_READ_ONLY_CHECKOUT_REQUIRED')
    for parent in root.parents:
        st = parent.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise GuardError('SOURCE_PARENT_SYMLINK_UNTRUSTED')
        if _write_permission(st.st_mode, uid, gid, groups, st):
            if not (st.st_mode & stat.S_ISVTX and st.st_uid != uid):
                raise GuardError('SOURCE_PARENT_WRITABLE_BY_TEST_ACTOR')
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in ['.'] + dirs + files:
            path = Path(base) if name == '.' else Path(base, name)
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise GuardError('SOURCE_SYMLINK_REQUIRES_TRUSTED_SANDBOX')
            if st.st_uid == uid:
                # An owner can chmod a 0400 file to 0600 and then modify it.
                raise GuardError('SOURCE_OWNED_BY_TEST_ACTOR')
            if _write_permission(st.st_mode, uid, gid, groups, st):
                raise GuardError('SOURCE_WRITABLE_BY_TEST_ACTOR')


def run_isolated_test(plan: dict, argv: list[str], *, user: str, evidence_dir: Path, timeout: int = 120) -> dict:
    """Run one test with actual source write denial via existing unprivileged OS uid.

    This protects only the pinned checkout, not network/production effects; route
    potentially destructive tests through their separately authorized lab policy.
    Caller must not have direct writable tool paths outside this executor.
    """
    verdict = evaluate(plan, 'test_run')
    if verdict['status'] != 'ALLOW':
        raise GuardError(verdict['reason'])
    if os.geteuid() != 0:
        raise GuardError('ROOT_LAUNCHER_REQUIRED_TO_DROP_PRIVILEGES')
    acct = pwd.getpwnam(user)
    if acct.pw_uid == 0 or acct.pw_name == 'root':
        raise GuardError('ROOT_TEST_ACTOR_FORBIDDEN')
    # Prevent opt-in test actor with administrator privileges from running in a
    # nominal read-only lane, even when the source mode bits look safe.
    groups = set(os.getgrouplist(acct.pw_name, acct.pw_gid))
    for dangerous in ('sudo', 'wheel', 'docker', 'lxd'):
        try:
            import grp
            if grp.getgrnam(dangerous).gr_gid in groups:
                raise GuardError('PRIVILEGED_TEST_ACTOR_FORBIDDEN')
        except KeyError:
            pass
    root = Path(plan['worktree']).resolve()
    evidence = evidence_dir.resolve()
    if _inside(evidence, root) or not evidence.is_dir() or evidence_dir.is_symlink():
        raise GuardError('EVIDENCE_ROOT_NOT_ISOLATED')
    _assert_source_read_only(root, acct.pw_uid, acct.pw_gid, groups)
    if not _write_permission(evidence.stat().st_mode, acct.pw_uid, acct.pw_gid, groups, evidence.stat()):
        raise GuardError('EVIDENCE_DIR_NOT_WRITABLE_BY_ACTOR')
    if not argv or not Path(argv[0]).is_absolute() or timeout <= 0 or timeout > 3600:
        raise GuardError('COMMAND_OR_TIMEOUT_INVALID')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(evidence), 'TMPDIR': str(evidence),
           'XDG_CACHE_HOME': str(evidence), 'PYTHONDONTWRITEBYTECODE': '1',
           'LANG': 'C', 'LC_ALL': 'C'}

    capture_limit = OUTPUT_LIMIT_BYTES

    def drop():
        # Test outputs and artifacts should not default to world-readable.
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_FSIZE, (capture_limit, capture_limit))
        os.setgroups([])
        os.setgid(acct.pw_gid)
        os.setuid(acct.pw_uid)

    # Never echo arbitrary test output (it may contain tokens or user data).
    # A private temporary regular file avoids unbounded Python PIPE memory;
    # RLIMIT_FSIZE bounds disk growth and the output is hashed, not returned.
    with tempfile.TemporaryFile(mode='w+b') as capture:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=capture, stderr=subprocess.STDOUT,
                                preexec_fn=drop, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise GuardError('ISOLATED_TEST_TIMEOUT')
        capture.flush()
        size = os.fstat(capture.fileno()).st_size
        capture.seek(0)
        output_sha256 = hashlib.file_digest(capture, 'sha256').hexdigest()
    if size >= capture_limit:
        raise GuardError('TEST_OUTPUT_LIMIT_EXCEEDED')
    # Do not promote the result if any protected source boundary drifted.
    after = evaluate(plan, 'test_run')
    if after['status'] != 'ALLOW':
        raise GuardError('SOURCE_CHANGED_DURING_TEST:' + after['reason'])
    return {'status': 'EXECUTED', 'exit_code': proc.returncode,
            'output_bytes': size, 'stdout_sha256': output_sha256,
            'pinned_head': plan['pinned_head'],
            'source_write_protected': True,
            'product_runtime_qualified': False}



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['replay', 'check', 'run-test'])
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--effect', default='read')
    parser.add_argument('--user')
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--timeout', type=int, default=120)
    args, command = parser.parse_known_args()
    try:
        plan = json.loads(args.plan.read_text())
        if args.mode != 'run-test' and command:
            raise GuardError('UNEXPECTED_ARGUMENTS')
        if args.mode == 'replay':
            result = replay(plan)
        elif args.mode == 'check':
            result = evaluate(plan, args.effect)
        else:
            if not args.user or not args.evidence_dir or not command:
                raise GuardError('ISOLATED_TEST_ARGUMENTS_REQUIRED')
            cmd = command[1:] if command[0] == '--' else command
            result = run_isolated_test(plan, cmd, user=args.user,
                evidence_dir=args.evidence_dir, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get('status') in ('ALLOW', 'EXECUTED') and result.get('exit_code', 0) == 0 else 2
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(_result('BLOCK', str(exc) if isinstance(exc, GuardError) else 'GUARD_INPUT_OR_EXECUTION_INVALID')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
