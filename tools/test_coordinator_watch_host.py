#!/usr/bin/env python3
"""Deterministic regressions for the coordinator watch run-once host."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coordinator import subject_version  # noqa: E402
from coordinator_watch import evaluate  # noqa: E402
import coordinator_watch_collect as collect  # noqa: E402
import coordinator_watch_effects  # noqa: E402
import coordinator_watch_host  # noqa: E402
from coordinator_watch_collect import collect_authoritative  # noqa: E402
from coordinator_watch_effects import send_effect  # noqa: E402
from coordinator_watch_host import (  # noqa: E402
    HostRequestError,
    canonical_state_paths,
    canonical_watch_dir,
    external_effect,
    main,
    owner_notice_effect,
    run_once,
)
import skills_contract  # noqa: E402
import skills_contract_fixtures as fixtures  # noqa: E402
from work_admission import canonicalize_worktree  # noqa: E402

FIXTURES = ROOT / "tools" / "fixtures" / "coordinator-watch"
SCHEMA = json.loads((ROOT / "schemas" / "coordinator-watch-host.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
SHA = "a" * 40
OTHER = "b" * 40
BRANCH = "feat/watch-host"
TARGET = "65"
REPO = "datarelay-labs/engineering-system"
WORKSTREAM = "p2-coordinator"

GH_SCRIPT = """#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["WATCH_HOST_GH_STATE"]
state = json.loads(open(state_path, encoding="utf-8").read())
argv = sys.argv[1:]
joined = " ".join(argv)
if "--method" in argv and "POST" in argv and "comments" in joined:
    with open(state["log"], "a", encoding="utf-8") as handle:
        handle.write("comment\\n")
    sys.stdout.write(json.dumps({"id": state.get("comment_id", 4242)}))
    raise SystemExit(0)
if argv[:2] == ["issue", "view"]:
    bodies = state["bodies"]
    if not bodies:
        sys.stderr.write("missing issue body\\n")
        raise SystemExit(2)
    body = bodies.pop(0)
    state["bodies"] = bodies
    open(state_path, "w", encoding="utf-8").write(json.dumps(state))
    sys.stdout.write(json.dumps({"number": int(argv[2]), "body": body}))
    raise SystemExit(0)
if argv[:1] == ["api"] and argv[1].endswith("/status"):
    sys.stdout.write(json.dumps({"state": state["ci"], "sha": state["sha"]}))
    raise SystemExit(0)
if argv[:1] == ["api"] and "/commits/" in argv[1]:
    queue = state.get("sha_queue") or []
    if queue:
        state["sha"] = queue.pop(0)
        state["sha_queue"] = queue
        open(state_path, "w", encoding="utf-8").write(json.dumps(state))
    sys.stdout.write(json.dumps({"sha": state["sha"]}))
    raise SystemExit(0)
if argv[:2] == ["pr", "list"]:
    if state.get("pr"):
        sys.stdout.write(json.dumps([{"number": 60, "state": "OPEN", "headRefOid": state["sha"]}]))
    else:
        sys.stdout.write("[]")
    raise SystemExit(0)
sys.stderr.write("unexpected gh " + joined + "\\n")
raise SystemExit(2)
"""


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_schema(result: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(result), key=lambda item: list(item.path))
    assert not errors, errors[0].message


def assert_inert(result: dict) -> None:
    assert result["stops_unrelated_sessions"] is False
    assert result["mutates_existing_sessions"] is False
    assert result["spawns_process"] is False
    assert result["executes_command"] is False
    assert result["busy_loop"] is False
    assert result["sleeps"] is False
    assert result["cadence"] == "EXTERNAL"
    delivered = result["result"] == "DELIVERED" and bool(result["wakes"] or result["resumes"])
    assert result["mutates_github"] is delivered
    assert result["actions_delivered"] == result["wakes"] + result["resumes"] + result["notifications"]
    assert result["actions_delivered"] in (0, 1)


def render_body(facts: dict, branch: str) -> str:
    packet = facts["packet"]
    lines = [
        f"TARGET_REPO={packet['repository']}",
        f"WORKSTREAM={packet['workstream']}",
        f"STATUS={packet['status']}",
        f"PRIORITY={packet['priority']}",
        f"INTENT_REVISION={packet['intent_revision']}",
        f"CHANGE_RISK={packet['change_risk']}",
        f"TASK_KIND={packet['task_kind']}",
        f"BRANCH={branch}",
    ]
    for dep in packet.get("dependencies") or []:
        lines.append(f"DEPENDENCY={dep['workstream']}:{dep['status']}")
    lines.append(f"RESOURCE={facts['resource']['result']}")
    admission = facts["admission"]
    lines.append(f"ADMISSION={admission['decision']}")
    if admission.get("deny_class"):
        lines.append(f"ADMISSION_DENY_CLASS={admission['deny_class']}")
    git = facts["git"]
    lines.append(f"GIT_DIRTY={'true' if git.get('dirty') else 'false'}")
    lines.append(f"GIT_UNPUSHED={'true' if git.get('unpushed') else 'false'}")
    worker = facts["worker"]
    if worker.get("present"):
        lines.append("WORKER_PRESENT=true")
        lines.append(f"WORKER_STATUS={worker['status']}")
        lines.append(f"WORKER_STARTING_INTENT_REVISION={worker['starting_intent_revision']}")
        lines.append(f"PROGRESS_EVIDENCE={'true' if worker.get('progress_evidence') else 'false'}")
    else:
        lines.append("WORKER_PRESENT=false")
    lines.append(f"MUTATION_AMBIGUOUS={'true' if facts['mutation']['ambiguous'] else 'false'}")
    publication = facts["publication"]
    lines.append(f"PUBLICATION_AUDIT={publication['coordinator_audit']}")
    if publication.get("authorized_intent_revision"):
        lines.append(f"PUBLICATION_REVISION={publication['authorized_intent_revision']}")
    return "\n".join(lines) + "\n"


def gh_state(base: Path, facts: dict, branch: str, *, bodies: list[str] | None = None, ci: str = "success", pr: bool = True, sha: str | None = None, sha_queue: list[str] | None = None) -> dict:
    if sha is None:
        pinned = collect._TEST_WORKTREE
        if pinned is not None:
            completed = subprocess.run(
                ["/usr/bin/git", "-C", str(pinned), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0 and len(completed.stdout.strip()) == 40:
                sha = completed.stdout.strip().lower()
        if sha is None:
            sha = SHA
    body = render_body(facts, branch)
    return {
        "bodies": bodies if bodies is not None else [body, body, body, body],
        "sha": sha,
        "sha_queue": sha_queue or [],
        "ci": ci,
        "pr": pr,
        "log": str(base / "comments.log"),
        "comment_id": 4242,
    }


@contextlib.contextmanager
def fake_gh(base: Path, state: dict) -> Iterator[Path]:
    bindir = base / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "gh"
    script.write_text(GH_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    shadow = base / "shadow-bin"
    shadow.mkdir(exist_ok=True)
    shadow_gh = shadow / "gh"
    shadow_log = base / "shadow-gh.log"
    shadow_gh.write_text(
        "#!/usr/bin/env python3\n"
        f"open({str(shadow_log)!r}, 'a', encoding='utf-8').write('shadow\\n')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    shadow_gh.chmod(shadow_gh.stat().st_mode | stat.S_IEXEC)
    state_path = base / "gh-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    previous_path = os.environ.get("PATH", "")
    previous_state = os.environ.get("WATCH_HOST_GH_STATE")
    previous_gh = collect._TEST_TRUSTED_GH
    os.environ["PATH"] = str(shadow) + os.pathsep + previous_path
    os.environ["WATCH_HOST_GH_STATE"] = str(state_path)
    collect._TEST_TRUSTED_GH = script
    try:
        yield state_path
    finally:
        collect._TEST_TRUSTED_GH = previous_gh
        os.environ["PATH"] = previous_path
        if previous_state is None:
            os.environ.pop("WATCH_HOST_GH_STATE", None)
        else:
            os.environ["WATCH_HOST_GH_STATE"] = previous_state


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


HEALTHY_MEMINFO = "MemTotal: 2000000 kB\nMemAvailable: 1500000 kB\nSwapTotal: 0 kB\n"
STARVED_MEMINFO = "MemTotal: 2000000 kB\nMemAvailable: 1 kB\nSwapTotal: 0 kB\n"


def init_observed_worktree(path: Path, *, dirty: bool, unpushed: bool, branch: str = BRANCH) -> None:
    remote = path.parent / "remotes" / "datarelay-labs" / "engineering-system.git"
    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/git", "init", "--bare", "-b", branch, str(remote)], check=True, capture_output=True)
    subprocess.run(["/usr/bin/git", "init", "-b", branch, str(path)], check=True, capture_output=True)
    subprocess.run(["/usr/bin/git", "-C", str(path), "remote", "add", "origin", str(remote)], check=True, capture_output=True)
    (path / "README").write_text("base\n", encoding="utf-8")
    git = ["/usr/bin/git", "-C", str(path), "-c", "user.email=watch@example.com", "-c", "user.name=watch"]
    subprocess.run([*git, "add", "README"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-m", "base"], check=True, capture_output=True)
    subprocess.run(["/usr/bin/git", "-C", str(path), "push", "-u", "origin", "HEAD"], check=True, capture_output=True)
    if unpushed:
        (path / "README").write_text("extra\n", encoding="utf-8")
        subprocess.run([*git, "add", "README"], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-m", "extra"], check=True, capture_output=True)
    if dirty:
        (path / "DIRTY").write_text("dirty\n", encoding="utf-8")


def write_agent(path: Path, worktree: Path, *, present: bool) -> None:
    if present:
        body = (
            "1 persistent sessions:\n"
            "Task: Work Resume\n"
            "  Status: Attached (1 client)\n"
            "  Session: cursor-watch-test\n"
            f"  Workspace: {worktree}\n"
        )
    else:
        body = "No Cursor-managed persistent sessions.\n"
    path.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.stdout.write({body!r})\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def write_claim(directory: Path, worktree: Path) -> None:
    directory.mkdir()
    (directory / "claim.json").write_text(
        json.dumps(
            {
                "claim_id": "watch-worker",
                "repository": REPO,
                "workstream": WORKSTREAM,
                "intent_revision": 3,
                "worktree": canonicalize_worktree(str(worktree)),
                "owned_paths": ["tools/"],
                "status": "ACTIVE",
                "dirty": False,
                "unpushed": False,
                "ambiguous": False,
            }
        ),
        encoding="utf-8",
    )


@contextlib.contextmanager
def observed_machine(
    base: Path,
    *,
    dirty: bool,
    unpushed: bool,
    session: bool,
    claim: bool,
    meminfo: list[str],
    local_branch: str = BRANCH,
    claim_worktree: Path | None = None,
) -> Iterator[Path]:
    worktree = base / "worktree"
    worktree.mkdir()
    init_observed_worktree(worktree, dirty=dirty, unpushed=unpushed, branch=local_branch)
    agent = base / "agent"
    write_agent(agent, worktree, present=session)
    claim_dir = base / "claims"
    if claim:
        write_claim(claim_dir, claim_worktree or worktree)
    previous = (
        collect._TEST_WORKTREE,
        collect._TEST_TRUSTED_AGENT,
        collect._TEST_CLAIM_DIR,
        collect._TEST_MEMINFO_BODIES,
    )
    collect._TEST_WORKTREE = worktree
    collect._TEST_TRUSTED_AGENT = agent
    collect._TEST_CLAIM_DIR = claim_dir if claim else None
    collect._TEST_MEMINFO_BODIES = list(meminfo)
    try:
        yield worktree
    finally:
        (
            collect._TEST_WORKTREE,
            collect._TEST_TRUSTED_AGENT,
            collect._TEST_CLAIM_DIR,
            collect._TEST_MEMINFO_BODIES,
        ) = previous


def comment_count(base: Path) -> int:
    log = base / "comments.log"
    if not log.exists():
        return 0
    return len([line for line in log.read_text(encoding="utf-8").splitlines() if line == "comment"])


@contextlib.contextmanager
def host_env(base: Path) -> Iterator[None]:
    previous = coordinator_watch_host._TEST_HOST_STATE_ROOT
    coordinator_watch_host._TEST_HOST_STATE_ROOT = base.resolve()
    try:
        yield
    finally:
        coordinator_watch_host._TEST_HOST_STATE_ROOT = previous


def request_for(facts: dict, branch: str = BRANCH) -> dict:
    return {
        "schema_version": 1,
        "target_repo": facts["packet"]["repository"],
        "workstream": facts["packet"]["workstream"],
        "watch_class": facts["watch"]["watch_class"],
        "branch": branch,
        "effect_target_id": TARGET,
        "work_budget": 1,
    }


class SignedDispatch:
    def __init__(self, base: Path, effect: dict, dispatch_id: str):
        self.base = base
        self.effect = effect
        self.dispatch_id = dispatch_id
        self.previous_anchor = skills_contract._TEST_TRUST_ANCHOR_PATH
        self.previous_replay = skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE
        self.previous_store = skills_contract._TEST_REPLAY_STORE

    def __enter__(self) -> dict[str, str]:
        priv, pub = fixtures.generate_keypair(self.base / "keys")
        _, _, digest = skills_contract.load_effective_state(ROOT)
        binding = self.base / "binding.json"
        dispatch = self.base / "dispatch.json"
        fixtures.write_binding_assertion(
            binding,
            private_key=priv,
            public_key=pub,
            profile="external_write",
            policy_digest=digest,
            authority_permission="admin",
            approved_classes=["external_write"],
        )
        fixtures.write_dispatch_assertion(
            dispatch,
            private_key=priv,
            public_key=pub,
            tool_id="network.post",
            classes=skills_contract.DEFAULT_TOOL_REGISTRY["network.post"],
            policy_digest=digest,
            request_payload=self.effect,
            dispatch_id=self.dispatch_id,
            expires_at_unix=int(time.time()) + 3600,
        )
        skills_contract._TEST_TRUST_ANCHOR_PATH = pub
        skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = True
        skills_contract._TEST_REPLAY_STORE = set()
        skills_contract._TEST_DISPATCH_RESERVATIONS = {}
        return {"binding_assertion": str(binding), "dispatch_assertion": str(dispatch)}

    def __exit__(self, *_args: object) -> None:
        skills_contract._TEST_TRUST_ANCHOR_PATH = self.previous_anchor
        skills_contract._TEST_REPLAY_BOUNDARY_AVAILABLE = self.previous_replay
        skills_contract._TEST_REPLAY_STORE = self.previous_store
        skills_contract._TEST_DISPATCH_RESERVATIONS = None


def signed_effect(base: Path, facts: dict, branch: str, state: dict) -> dict:
    with fake_gh(base / "preview-gh", json.loads(json.dumps(state))):
        collected = collect_authoritative(
            repository=facts["packet"]["repository"],
            workstream=facts["packet"]["workstream"],
            issue_id=TARGET,
            watch_class=facts["watch"]["watch_class"],
        )
    from coordinator import normalize_facts

    normalized = normalize_facts({key: value for key, value in collected["facts"].items() if key != "watch"})
    result = evaluate(collected["facts"], {})
    if result["result"] == "NOTIFY_OWNER":
        return owner_notice_effect(result)
    return external_effect(
        result,
        repository=normalized["packet"]["repository"],
        workstream=normalized["packet"]["workstream"],
        intent_revision=normalized["packet"]["intent_revision"],
        subject_head=subject_version(normalized),
        status=normalized["packet"]["status"],
        branch=collected["branch"],
        effect_target_id=TARGET,
    )


def test_unchanged_wait_takes_no_action() -> None:
    facts = load_fixture("01-ci-pending.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, ci="pending", pr=True, bodies=[render_body(facts, BRANCH)])
        with fake_gh(base, state), host_env(base):
            result = run_once(request_for(facts))
        assert_schema(result)
        assert_inert(result)
        assert result["result"] == "NO_ACTION"
        assert result["watch_result"] == "RECHECK_LATER"
        assert result["actions_delivered"] == 0
        assert comment_count(base) == 0


def test_exact_head_ci_wakes_once_and_replay_dedups() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH)
            effect = signed_effect(base, facts, BRANCH, state)
            with SignedDispatch(base, effect, "watch-host-wake-1") as verification:
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    first = run_once(request)
                    second = run_once(request)
        assert_schema(first)
        assert_inert(first)
        assert first["result"] == "DELIVERED"
        assert first["wakes"] == 1
        assert first["mutates_github"] is True
        assert second["result"] in {"DEDUP", "NO_ACTION"}
        assert second["wakes"] == 0
        assert comment_count(base) == 1


def test_progress_resumes_once() -> None:
    facts = load_fixture("06-worker-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=True, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH, pr=False)
            effect = signed_effect(base, facts, BRANCH, state)
            with SignedDispatch(base, effect, "watch-host-resume-1") as verification:
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    result = run_once(request)
        assert result["result"] == "DELIVERED"
        assert result["resumes"] == 1
        assert result["mutates_github"] is True
        assert comment_count(base) == 1


def _assert_unbound_session_does_not_resume(base: Path, facts: dict, state: dict) -> None:
    with fake_gh(base, state):
        collected = collect_authoritative(
            repository=REPO,
            workstream=WORKSTREAM,
            issue_id=TARGET,
            watch_class=facts["watch"]["watch_class"],
        )
        with host_env(base):
            result = run_once(request_for(facts))
    worker = collected["facts"]["worker"]
    assert worker.get("present") is not True
    assert worker.get("progress_evidence") is not True
    assert collected["facts"]["git"].get("dirty") is not True
    assert collected["facts"]["git"].get("unpushed") is not True
    assert result["watch_result"] != "RESUME_ADMITTED_WORKER"
    assert result["result"] != "DELIVERED"
    assert result["resumes"] == 0
    assert result["actions_delivered"] == 0
    assert comment_count(base) == 0


def test_wrong_branch_session_does_not_resume() -> None:
    facts = load_fixture("06-worker-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base,
            dirty=True,
            unpushed=True,
            session=True,
            claim=True,
            meminfo=[HEALTHY_MEMINFO],
            local_branch="main",
        ):
            state = gh_state(base, facts, BRANCH, pr=False)
            _assert_unbound_session_does_not_resume(base, facts, state)


def test_wrong_head_session_does_not_resume() -> None:
    facts = load_fixture("06-worker-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=True, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH, pr=False, sha=OTHER)
            _assert_unbound_session_does_not_resume(base, facts, state)


def test_different_claim_worktree_does_not_resume() -> None:
    facts = load_fixture("06-worker-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        other = base / "other-worktree"
        other.mkdir()
        with observed_machine(
            base,
            dirty=True,
            unpushed=False,
            session=True,
            claim=True,
            meminfo=[HEALTHY_MEMINFO],
            claim_worktree=other,
        ):
            state = gh_state(base, facts, BRANCH, pr=False)
            with fake_gh(base, state):
                collected = collect_authoritative(
                    repository=REPO,
                    workstream=WORKSTREAM,
                    issue_id=TARGET,
                    watch_class=facts["watch"]["watch_class"],
                )
                with host_env(base):
                    result = run_once(request_for(facts))
        worker = collected["facts"]["worker"]
        assert collected["facts"]["git"].get("dirty") is True
        assert worker.get("present") is not True
        assert worker.get("progress_evidence") is not True
        assert result["watch_result"] != "RESUME_ADMITTED_WORKER"
        assert result["result"] != "DELIVERED"
        assert result["resumes"] == 0
        assert result["actions_delivered"] == 0
        assert comment_count(base) == 0


def test_liveness_without_progress_does_not_resume() -> None:
    facts = load_fixture("05-worker-no-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH, pr=False, bodies=[render_body(facts, BRANCH)])
            with fake_gh(base, state), host_env(base):
                result = run_once(request_for(facts))
        assert result["result"] == "NO_ACTION"
        assert result["resumes"] == 0
        assert comment_count(base) == 0


def test_revision_change_before_action_delivers_nothing() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    changed = json.loads(json.dumps(facts))
    changed["packet"]["intent_revision"] = 4
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, bodies=[render_body(facts, BRANCH), render_body(changed, BRANCH)])
        with fake_gh(base, state), host_env(base):
            result = run_once(request_for(facts))
        assert result["result"] == "STALE_RECONCILE"
        assert result["actions_delivered"] == 0
        assert comment_count(base) == 0


def test_subject_change_before_action_delivers_nothing() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, sha_queue=[SHA, OTHER])
        with fake_gh(base, state), host_env(base):
            result = run_once(request_for(facts))
        assert result["result"] == "STALE_RECONCILE"
        assert result["wakes"] == 0
        assert comment_count(base) == 0


def test_resource_block_before_resume_leaves_sessions_untouched() -> None:
    facts = load_fixture("06-worker-progress.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        body = render_body(facts, BRANCH)
        with observed_machine(
            base,
            dirty=True,
            unpushed=False,
            session=True,
            claim=True,
            meminfo=[HEALTHY_MEMINFO, STARVED_MEMINFO],
        ):
            state = gh_state(base, facts, BRANCH, pr=False, bodies=[body, body])
            with fake_gh(base, state), host_env(base):
                result = run_once(request_for(facts))
        assert_inert(result)
        assert result["result"] == "RESOURCE_BLOCKED"
        assert result["resumes"] == 0
        assert comment_count(base) == 0


def test_requested_branch_mismatch_consumes_nothing() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, bodies=[render_body(facts, BRANCH)])
        effect = signed_effect(base, facts, BRANCH, gh_state(base, facts, BRANCH))
        with SignedDispatch(base, effect, "watch-host-branch") as verification:
            request = request_for(facts, branch="feat/other")
            request["verification"] = verification
            with fake_gh(base, state), host_env(base):
                result = run_once(request)
            assert result["result"] == "STALE_RECONCILE"
            assert result["actions_delivered"] == 0
            assert comment_count(base) == 0
            assert "watch-host-branch" not in skills_contract._TEST_REPLAY_STORE


def test_forged_authoritative_file_cannot_authorize() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        forged = base / "authoritative.json"
        forged.write_text('{"forged": true}\n', encoding="utf-8")
        request = request_for(facts)
        request["authoritative_facts_path"] = str(forged)
        with host_env(base):
            try:
                run_once(request)
            except HostRequestError as exc:
                assert "not authoritative" in exc.reason
            else:
                raise AssertionError("caller fact path was accepted")
        assert forged.read_text(encoding="utf-8") == '{"forged": true}\n'
        assert comment_count(base) == 0


def test_noncanonical_lock_cannot_escape_or_split_identity() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        sentinel = base / "sentinel"
        sentinel.write_text("ORIGINAL-SENTINEL", encoding="utf-8")
        state = gh_state(base, facts, BRANCH, bodies=[render_body(facts, BRANCH)])
        request = request_for(facts)
        request["lock_path"] = str(sentinel)
        with fake_gh(base, state), host_env(base):
            try:
                run_once(request)
            except HostRequestError as exc:
                assert "canonical" in exc.reason
            else:
                raise AssertionError("caller lock path was accepted")
        assert sentinel.read_text(encoding="utf-8") == "ORIGINAL-SENTINEL"
        directory = canonical_watch_dir(
            base.resolve(),
            repository=REPO,
            workstream=WORKSTREAM,
            intent_revision=3,
            watch_class="exact_head_ci",
            subject=SHA,
        )
        other = canonical_watch_dir(
            base.resolve(),
            repository=REPO,
            workstream=WORKSTREAM,
            intent_revision=3,
            watch_class="exact_head_ci",
            subject=OTHER,
        )
        assert directory != other
        assert canonical_state_paths(directory)["lock_path"] != canonical_state_paths(other)["lock_path"]


def test_symlink_lock_cannot_escape_state_root() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "state"
        root.mkdir()
        sentinel = base / "sentinel"
        sentinel.write_text("ORIGINAL-SENTINEL", encoding="utf-8")
        directory = canonical_watch_dir(
            root.resolve(),
            repository=REPO,
            workstream=WORKSTREAM,
            intent_revision=3,
            watch_class="exact_head_ci",
            subject=SHA,
        )
        directory.mkdir()
        canonical_state_paths(directory)["lock_path"].symlink_to(sentinel)
        state = gh_state(base, facts, BRANCH, bodies=[render_body(facts, BRANCH)])
        with fake_gh(base, state), host_env(root):
            try:
                run_once(request_for(facts))
            except HostRequestError as exc:
                assert "symlink" in exc.reason
            else:
                raise AssertionError("symlink lock was followed")
        assert sentinel.read_text(encoding="utf-8") == "ORIGINAL-SENTINEL"


def test_same_identity_shares_one_lock() -> None:
    facts = load_fixture("01-ci-pending.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        directory = canonical_watch_dir(
            base.resolve(),
            repository=REPO,
            workstream=WORKSTREAM,
            intent_revision=3,
            watch_class=facts["watch"]["watch_class"],
            subject=SHA,
        )
        directory.mkdir(parents=True)
        lock_path = canonical_state_paths(directory)["lock_path"]
        holder_code = (
            "import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "sys.stdout.write('held\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code, str(lock_path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            state = gh_state(base, facts, BRANCH, ci="pending", bodies=[render_body(facts, BRANCH)])
            with fake_gh(base, state), host_env(base):
                result = run_once(request_for(facts))
        finally:
            holder.kill()
            holder.wait(timeout=5)
        assert result["result"] == "LOCK_HELD"
        assert result["actions_delivered"] == 0
        assert comment_count(base) == 0


def test_pre_send_crash_is_retryable_once() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        body = render_body(facts, BRANCH)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH, bodies=[body, body, body, body])
            effect = signed_effect(base, facts, BRANCH, state)
            original = coordinator_watch_host._commit_journal

            def crash_before_send(path: Path, payload: dict[str, str]) -> None:
                if payload["phase"] == "SENDING":
                    raise SystemExit(86)
                original(path, payload)

            with SignedDispatch(base, effect, "watch-host-presend") as verification:
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    coordinator_watch_host._commit_journal = crash_before_send
                    try:
                        try:
                            run_once(request)
                        except SystemExit as exc:
                            assert exc.code == 86
                        else:
                            raise AssertionError("pre-send crash did not surface")
                    finally:
                        coordinator_watch_host._commit_journal = original
                    assert comment_count(base) == 0
                    assert "watch-host-presend" not in skills_contract._TEST_REPLAY_STORE
                    result = run_once(request)
                assert result["result"] == "DELIVERED"
                assert comment_count(base) == 1
                assert "watch-host-presend" in skills_contract._TEST_REPLAY_STORE


def test_post_send_crash_is_not_retried() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        body = render_body(facts, BRANCH)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH, bodies=[body, body, body, body])
            effect = signed_effect(base, facts, BRANCH, state)
            original = coordinator_watch_host._commit_journal

            def crash_before_receipt(path: Path, payload: dict[str, str]) -> None:
                if payload["phase"] == "RECEIPT":
                    raise SystemExit(87)
                original(path, payload)

            with SignedDispatch(base, effect, "watch-host-postsend") as verification:
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    coordinator_watch_host._commit_journal = crash_before_receipt
                    try:
                        try:
                            run_once(request)
                        except SystemExit as exc:
                            assert exc.code == 87
                        else:
                            raise AssertionError("post-send crash did not surface")
                    finally:
                        coordinator_watch_host._commit_journal = original
                    assert comment_count(base) == 1
                    second = run_once(request)
                assert second["result"] == "RECONCILE_AMBIGUOUS"
                assert second["actions_delivered"] == 0
                assert comment_count(base) == 1
                assert "watch-host-postsend" not in skills_contract._TEST_REPLAY_STORE


def test_owner_notice_is_verified_info() -> None:
    facts = load_fixture("10-blocked.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH)
        effect = signed_effect(base, facts, BRANCH, state)
        assert effect["level"] == "INFO"
        token_dir = base / "telegram"
        token_dir.mkdir()
        (token_dir / "telegram-bot-token").write_text("host-token", encoding="utf-8")
        (token_dir / "telegram-chat-id").write_text("host-chat", encoding="utf-8")
        seen: dict[str, object] = {}

        class Response:
            def read(self) -> bytes:
                return b'{"ok":true,"result":{"message_id":77}}'

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        def urlopen(request, timeout=0):  # noqa: ANN001
            seen["url"] = request.full_url
            seen["data"] = request.data
            seen["timeout"] = timeout
            return Response()

        previous = coordinator_watch_effects._TEST_TELEGRAM_DIR
        coordinator_watch_effects._TEST_TELEGRAM_DIR = token_dir
        original = coordinator_watch_effects.urllib.request.urlopen
        coordinator_watch_effects.urllib.request.urlopen = urlopen
        try:
            with SignedDispatch(base, effect, "watch-host-notice-1") as verification:
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    first = run_once(request)
                    second = run_once(request)
        finally:
            coordinator_watch_effects._TEST_TELEGRAM_DIR = previous
            coordinator_watch_effects.urllib.request.urlopen = original
        assert first["result"] == "DELIVERED"
        assert first["notifications"] == 1
        assert first["notification_level"] == "INFO"
        assert first["notification_delivery"] == "VERIFIED"
        assert first["mutates_github"] is False
        assert str(seen["url"]).startswith("https://api.telegram.org/bot")
        assert b"LEVEL=INFO" in bytes(seen["data"])
        assert b"COMPLETE" not in bytes(seen["data"])
        assert second["result"] in {"DEDUP", "NO_ACTION"}
        assert second["notifications"] == 0
        assert send_effect("NOTIFY_OWNER", {**effect, "level": "COMPLETE"})["outcome"] == "NOT_SENT"


def test_unconfirmed_send_is_not_delivery() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH)
            effect = signed_effect(base, facts, BRANCH, state)
            original = coordinator_watch_effects.send_github_comment

            def fail(repository: str, issue_id: str, body: str) -> dict[str, str]:
                return {"outcome": "AMBIGUOUS", "receipt": "", "level": "NONE"}

            coordinator_watch_effects.send_github_comment = fail
            try:
                with SignedDispatch(base, effect, "watch-host-ambiguous") as verification:
                    request = request_for(facts)
                    request["verification"] = verification
                    with fake_gh(base, state), host_env(base):
                        result = run_once(request)
                    assert result["result"] == "RECONCILE_AMBIGUOUS"
                    assert result["result"] != "DELIVERED"
                    assert result["actions_delivered"] == 0
                    assert "watch-host-ambiguous" not in skills_contract._TEST_REPLAY_STORE
            finally:
                coordinator_watch_effects.send_github_comment = original


def test_path_shadow_gh_is_never_executed() -> None:
    facts = load_fixture("01-ci-pending.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        shadow = base / "shadow-bin"
        shadow.mkdir()
        log = base / "shadow-gh.log"
        script = shadow / "gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            f"open({str(log)!r}, 'a', encoding='utf-8').write('shadow\\n')\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        previous_path = os.environ.get("PATH", "")
        previous_gh = collect._TEST_TRUSTED_GH
        os.environ["PATH"] = str(shadow) + os.pathsep + previous_path
        collect._TEST_TRUSTED_GH = None
        try:
            try:
                collect_authoritative(
                    repository=REPO,
                    workstream=WORKSTREAM,
                    issue_id=TARGET,
                    watch_class=facts["watch"]["watch_class"],
                )
            except collect.CollectError as exc:
                assert "trusted gh" in exc.reason
            else:
                raise AssertionError("PATH gh was accepted")
        finally:
            os.environ["PATH"] = previous_path
            collect._TEST_TRUSTED_GH = previous_gh
        assert not log.exists()
        state = gh_state(base, facts, BRANCH, ci="pending", bodies=[render_body(facts, BRANCH)])
        with fake_gh(base, state), host_env(base):
            result = run_once(request_for(facts))
        assert result["result"] == "NO_ACTION"
        assert not (base / "shadow-gh.log").exists() or (base / "shadow-gh.log").read_text(encoding="utf-8") == ""


def test_packet_text_cannot_falsify_machine_facts() -> None:
    facts = load_fixture("06-worker-progress.json")
    lied = json.loads(json.dumps(facts))
    lied["git"]["dirty"] = False
    lied["git"]["unpushed"] = False
    lied["worker"]["present"] = False
    lied["worker"]["progress_evidence"] = False
    lied["resource"]["result"] = "PASS"
    lied["admission"]["decision"] = "ALLOW"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=True, unpushed=True, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, lied, BRANCH, pr=False, bodies=[render_body(lied, BRANCH)])
            with fake_gh(base, state):
                collected = collect_authoritative(
                    repository=REPO,
                    workstream=WORKSTREAM,
                    issue_id=TARGET,
                    watch_class=facts["watch"]["watch_class"],
                )
        git = collected["facts"]["git"]
        worker = collected["facts"]["worker"]
        assert git["dirty"] is True
        assert git["unpushed"] is True
        assert worker["present"] is True
        assert worker["progress_evidence"] is True
        assert collected["facts"]["resource"]["result"] in {"PASS", "WARN"}
    clean = json.loads(json.dumps(facts))
    clean["worker"]["present"] = True
    clean["worker"]["progress_evidence"] = True
    clean["git"]["dirty"] = True
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=False, unpushed=False, session=False, claim=False, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, clean, BRANCH, pr=False, bodies=[render_body(clean, BRANCH)])
            with fake_gh(base, state):
                collected = collect_authoritative(
                    repository=REPO,
                    workstream=WORKSTREAM,
                    issue_id=TARGET,
                    watch_class=facts["watch"]["watch_class"],
                )
        assert collected["facts"]["worker"]["present"] is False
        assert collected["facts"]["git"]["dirty"] is False
        assert collected["facts"]["admission"]["decision"] == "UNKNOWN"


def test_unobserved_machine_facts_are_not_passing() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, bodies=[render_body(facts, BRANCH)])
        with fake_gh(base, state):
            collected = collect_authoritative(
                repository=REPO,
                workstream=WORKSTREAM,
                issue_id=TARGET,
                watch_class=facts["watch"]["watch_class"],
            )
    git = collected["facts"]["git"]
    assert "dirty" not in git
    assert "unpushed" not in git
    assert "worker" not in collected["facts"]
    assert collected["facts"]["resource"]["result"] == "UNKNOWN"
    assert collected["facts"]["admission"]["decision"] == "UNKNOWN"


def test_foreign_reservation_blocks_send() -> None:
    facts = load_fixture("02-ci-pass-exact.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with observed_machine(
            base, dirty=False, unpushed=False, session=True, claim=True, meminfo=[HEALTHY_MEMINFO]
        ):
            state = gh_state(base, facts, BRANCH)
            effect = signed_effect(base, facts, BRANCH, state)
            with SignedDispatch(base, effect, "watch-host-foreign") as verification:
                skills_contract.reserve_dispatch("watch-host-foreign", "ab" * 32, "other-attempt")
                request = request_for(facts)
                request["verification"] = verification
                with fake_gh(base, state), host_env(base):
                    result = run_once(request)
                assert result["result"] == "AUTHORITY_DENIED"
                assert result["actions_delivered"] == 0
                assert comment_count(base) == 0
                assert "watch-host-foreign" not in skills_contract._TEST_REPLAY_STORE


def test_source_boundaries() -> None:
    host = (ROOT / "tools" / "coordinator_watch_host.py").read_text(encoding="utf-8")
    effects = (ROOT / "tools" / "coordinator_watch_effects.py").read_text(encoding="utf-8")
    collector = (ROOT / "tools" / "coordinator_watch_collect.py").read_text(encoding="utf-8")
    for token in ("import subprocess", "subprocess.", "urllib", "socket", "time.sleep", "while ", "_TEST_EFFECT_EXECUTOR"):
        assert token not in host, token
    assert "api.telegram.org" in effects
    assert "shell=False" in effects and "shell=True" not in effects
    assert "collect_authoritative" in collector
    assert "authoritative_facts_path" not in collector


def test_cli_quiet_wait() -> None:
    facts = load_fixture("01-ci-pending.json")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = gh_state(base, facts, BRANCH, ci="pending", bodies=[render_body(facts, BRANCH)])
        path = base / "request.json"
        path.write_text(json.dumps(request_for(facts)), encoding="utf-8")
        buffer = io.StringIO()
        with fake_gh(base, state), host_env(base), contextlib.redirect_stdout(buffer):
            code = main(["run-once", "--request", str(path)])
    assert code == 0, buffer.getvalue()
    fields = dict(line.split("=", 1) for line in buffer.getvalue().splitlines())
    assert fields["RESULT"] == "NO_ACTION"
    assert fields["ACTIONS_DELIVERED"] == "0"
    assert fields["MUTATES_GITHUB"] == "NO"
    assert fields["EXECUTES_COMMAND"] == "NO"


def main_tests() -> int:
    test_unchanged_wait_takes_no_action()
    test_exact_head_ci_wakes_once_and_replay_dedups()
    test_progress_resumes_once()
    test_wrong_branch_session_does_not_resume()
    test_wrong_head_session_does_not_resume()
    test_different_claim_worktree_does_not_resume()
    test_liveness_without_progress_does_not_resume()
    test_revision_change_before_action_delivers_nothing()
    test_subject_change_before_action_delivers_nothing()
    test_resource_block_before_resume_leaves_sessions_untouched()
    test_requested_branch_mismatch_consumes_nothing()
    test_forged_authoritative_file_cannot_authorize()
    test_noncanonical_lock_cannot_escape_or_split_identity()
    test_symlink_lock_cannot_escape_state_root()
    test_same_identity_shares_one_lock()
    test_pre_send_crash_is_retryable_once()
    test_post_send_crash_is_not_retried()
    test_owner_notice_is_verified_info()
    test_unconfirmed_send_is_not_delivery()
    test_path_shadow_gh_is_never_executed()
    test_packet_text_cannot_falsify_machine_facts()
    test_unobserved_machine_facts_are_not_passing()
    test_foreign_reservation_blocks_send()
    test_source_boundaries()
    test_cli_quiet_wait()
    print("COORDINATOR_WATCH_HOST_TESTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_tests())
