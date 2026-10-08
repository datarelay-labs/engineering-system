#!/usr/bin/env python3
"""Deterministic audit/bootstrap helper for Engineering System adoption.

The tool automates mechanical installation only. It never overwrites existing
project files and never decides that project-specific rules are obsolete.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from functools import lru_cache

from execution_profile import (
    load_profile,
    retired_artifact_paths,
    retired_rule_present,
    rewrite_retired_text,
)

CANONICAL = Path(__file__).resolve().parents[1]
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
POLICY_EPOCH = 22
GOVERNANCE_EPOCH = 8

RULE_SURFACES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
)

# Provider-neutral knowledge-contract execution path. Optional organization/product
# derived-context adapters are intentionally not installed by universal adoption.
# The index itself is never created.
KNOWLEDGE_CONTRACT_MANAGED = (
    "tools/knowledge-contract.py",
    "schemas/knowledge-index.schema.json",
)

# Optional runtime-contract execution path. Installed when missing so adopted
# repositories can resolve health/smoke/E2E authorities. The runtime profile
# itself is never created.
RUNTIME_CONTRACT_MANAGED = (
    "tools/runtime-contract.py",
    "schemas/runtime-contract.schema.json",
)

# Optional skills/permission-contract execution path. Installed when missing so
# adopted AGENTS.md instructions resolve. The skills profile itself is never created.
SKILLS_CONTRACT_MANAGED = (
    "tools/skills-contract.py",
    "tools/work_packet_authority.py",
    "schemas/skills-contract.schema.json",
)

# Optional verification-contract helper, schemas, and the reused T4 verifier.
# `.engineering/verification.yaml` is never created. Test fixtures are not installed.
VERIFICATION_CONTRACT_MANAGED = (
    "tools/verification-contract.py",
    "tools/independent_verifier.py",
    "schemas/verification-contract.schema.json",
    "schemas/trust-evidence-receipt.schema.json",
    "schemas/trust-evidence-boundary.schema.json",
)

# Human-equivalent user acceptance evidence validator for user-facing releases.
USER_ACCEPTANCE_MANAGED = (
    "tools/user_acceptance_contract.py",
    "schemas/user-acceptance-evidence.schema.json",
)

# Repository-relative helpers referenced by the managed AGENTS execution rules.
# Keep the whole dependency closure together so an adopted repository never
# receives instructions for a helper that the managed snapshot did not install.
AGENT_RUNTIME_MANAGED = (
    "tools/work_admission.py",
    "tools/coordinator.py",
    "tools/coordinator_watch.py",
    "tools/coordinator_watch_collect.py",
    "tools/coordinator_watch_effects.py",
    "tools/coordinator_watch_host.py",
    "tools/worker_adapter.py",
    "schemas/coordinator-decision.schema.json",
    "schemas/coordinator-watch.schema.json",
    "schemas/coordinator-watch-host.schema.json",
    "schemas/worker-adapter-result.schema.json",
)

# Provider-neutral implementation preflight for direct Chat/remote mutation.
IMPLEMENTATION_PREFLIGHT_MANAGED = (
    "tools/implementation_preflight.py",
)

# Terminal owner notification is managed so adopted repositories can report
# bounded COMPLETE/BLOCKED outcomes. Delivery state is separate from engineering truth.
TERMINAL_COMPLETION_NOTIFY_MANAGED = (
    "tools/terminal_completion_notify.py",
)

# Selected execution profile and its provider-neutral validator are managed together.
EXECUTION_PROFILE_MANAGED = (
    ".engineering/execution-profile.yaml",
    "tools/execution_profile.py",
    "schemas/execution-profile.schema.json",
)

# Context-epoch projection is a provider-neutral managed helper.
CONTEXT_EPOCH_MANAGED = (
    "tools/context_epoch.py",
    "tools/handoff_contract.py",
)

# Exact-HEAD repository orientation is a provider-neutral managed helper.
ENGINEERING_CONTEXT_MANAGED = (
    "tools/engineering-context.py",
)

# Base-owned PR governance floor used by pull_request_target.
GOVERNANCE_FLOOR_MANAGED = (
    "tools/governance_floor.py",
)

# Deterministic changed-path -> affected-scenario selector used by the shared
# affected-tests workflow. Keep it baseline-managed with the workflow contract.
AFFECTED_TEST_SELECTION_MANAGED = (
    "tools/affected_test_selection.py",
)

WORK_PACKET_TEMPLATE_MANAGED = (
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
)

ENGINEERING_SYSTEM_DEPENDENCIES_MANAGED = (
    ".engineering/requirements-engineering-system.txt",
)

REQUIRED_MANAGED = (
    "AGENTS.md",
    ".engineering/project.yaml",
    ".engineering/tests.yaml",
    ".engineering/release.yaml",
    ".github/ISSUE_TEMPLATE/ai-work-packet.md",
    ".github/workflows/engineering-system.yml",
    *ENGINEERING_SYSTEM_DEPENDENCIES_MANAGED,
    *KNOWLEDGE_CONTRACT_MANAGED,
    *RUNTIME_CONTRACT_MANAGED,
    *SKILLS_CONTRACT_MANAGED,
    *VERIFICATION_CONTRACT_MANAGED,
    *USER_ACCEPTANCE_MANAGED,
    *AGENT_RUNTIME_MANAGED,
    *IMPLEMENTATION_PREFLIGHT_MANAGED,
    *TERMINAL_COMPLETION_NOTIFY_MANAGED,
    *EXECUTION_PROFILE_MANAGED,
    *CONTEXT_EPOCH_MANAGED,
    *ENGINEERING_CONTEXT_MANAGED,
    *GOVERNANCE_FLOOR_MANAGED,
    *AFFECTED_TEST_SELECTION_MANAGED,
)

EXECUTION_PROFILE_MARKER = "- **Execution profile authority:**"
EXECUTION_POLICY_MARKER = "- **Execute useful work continuously.**"
SUPERVISOR_POLICY_MARKER = "- **Product execution ownership / supervisor fallback:**"
NEXT_CHAT_BOOTSTRAP_POLICY_MARKER = "- **Next-chat bootstrap fast path:**"
VERIFIED_NEXT_CHAT_RESUME_POLICY_MARKER = "- **Verified next-chat resume:**"
EXTERNAL_WRITE_POLICY_MARKER = "- For ordinary authenticated GitHub Issue/PR coordination,"
USER_GATE_POLICY_MARKER = "- For `project.user_facing: true`,"
EXECUTION_RULES_HEADING = "## Execution rules"


def _selected_profile() -> dict[str, object]:
    return load_profile(CANONICAL)


def rewrite_retired_agent_rules(text: str) -> str:
    return rewrite_retired_text(text, _selected_profile())


def retired_agent_rules_present(text: str) -> bool:
    return retired_rule_present(text, _selected_profile())


def retired_agent_artifact_paths() -> tuple[str, ...]:
    return retired_artifact_paths(_selected_profile())


def _canonical_policy_line(marker: str, label: str) -> str:
    template = CANONICAL / "templates" / "AGENTS.md"
    if not template.is_file():
        raise SystemExit("FAIL canonical AGENTS template missing")
    matches = [
        line
        for line in template.read_text(encoding="utf-8").splitlines()
        if line.startswith(marker)
    ]
    if len(matches) != 1:
        raise SystemExit(f"FAIL canonical AGENTS template must contain exactly one managed {label} policy")
    return matches[0]


def canonical_execution_policy_line() -> str:
    return _canonical_policy_line(EXECUTION_POLICY_MARKER, "continuous-execution")


def canonical_managed_policy_lines() -> tuple[str, ...]:
    return (
        _canonical_policy_line(EXECUTION_PROFILE_MARKER, "execution-profile"),
        canonical_execution_policy_line(),
        _canonical_policy_line(SUPERVISOR_POLICY_MARKER, "product-supervisor-boundary"),
        _canonical_policy_line(NEXT_CHAT_BOOTSTRAP_POLICY_MARKER, "next-chat-bootstrap"),
        _canonical_policy_line(VERIFIED_NEXT_CHAT_RESUME_POLICY_MARKER, "verified-next-chat-resume"),
        _canonical_policy_line(EXTERNAL_WRITE_POLICY_MARKER, "external-write-scope"),
    )


def canonical_user_gate_policy_line() -> str:
    return _canonical_policy_line(USER_GATE_POLICY_MARKER, "user-gate")


@lru_cache(maxsize=32)
def _baseline_agents_templates(baseline: str) -> tuple[str, ...]:
    """Bounded, read-only migration provenance; never execute historical tools."""
    if FULL_SHA_RE.fullmatch(baseline) is None:
        return ()
    history = subprocess.run(
        ["git", "-C", str(CANONICAL), "log", "-32", "--format=%H", baseline,
         "--", "templates/AGENTS.md"], text=True, capture_output=True, check=False,
    )
    refs = [baseline]
    if history.returncode == 0:
        refs.extend(ref for ref in history.stdout.splitlines() if FULL_SHA_RE.fullmatch(ref))
    templates = []
    for ref in dict.fromkeys(refs):
        result = subprocess.run(
            ["git", "-C", str(CANONICAL), "show", f"{ref}:templates/AGENTS.md"],
            text=True, capture_output=True, check=False,
        )
        if result.returncode == 0 and result.stdout not in templates:
            templates.append(result.stdout)
    return tuple(templates)


def prior_agents_templates(root: Path) -> tuple[str, ...]:
    """Only the adopted immutable baseline and its canonical ancestors qualify."""
    import yaml
    try:
        project = yaml.safe_load((root / ".engineering/project.yaml").read_text()) or {}
        baseline = str((project.get("engineering_system") or {}).get("baseline") or "")
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return ()
    return _baseline_agents_templates(baseline)


def rewrite_legacy_coordination_rules(text: str) -> str:
    """Remove known obsolete normal-work gates; preserve product/risk policy."""
    legacy_gate = (
        "Before mutation, the external authenticated GitHub coordinator must verify the current Work Packet, "
        "author permission, repository, worktree, branch, exact HEAD, intent revision, change risk, and "
        "`IMPLEMENTER=CHATGPT_CHAT`. The worker-writable repository copy of "
        "`python3 tools/implementation_preflight.py check` is never mutation authority. Use the helper source "
        "from the immutable pinned Engineering System baseline through the isolated trusted launcher, "
        "capture the no-follow worktree identity, and require `IMPLEMENTATION_LOCAL_BINDING=PASS` with "
        "`MUTATION_AUTHORITY=NO`."
    )
    # Historical Atlas adopted a different *complete* coordinator ceremony.
    # Remove this known retired ordinary-implementation requirement verbatim.
    # An unknown/customized policy variant must remain intact for review, not
    # be deleted by a broad substring or regular expression.
    atlas_legacy_gate = (
        "Before mutation, the external authenticated GitHub coordinator must freshly verify the canonical "
        "Issue, author write/maintain/admin permission, TARGET_REPO, WORKSTREAM, BRANCH, "
        "LAST_VERIFIED_HEAD, INTENT_REVISION, `IMPLEMENTER=CHATGPT_CHAT`, CHANGE_RISK, and authorized "
        "worktree. Never treat the target worktree's `python3 tools/implementation_preflight.py check` as "
        "authoritative. Fetch the exact helper source from the immutable Engineering System baseline "
        "and execute it through fixed isolated `/usr/bin/python3 -I -` with cwd `/` and a controlled "
        "environment; capture worktree identity first and require `IMPLEMENTATION_LOCAL_BINDING=PASS`. "
        "The repository helper copy is parity/reference/test material only."
    )
    atlas_legacy_numbered_rule = (
        "14. ChatGPT Chat is the default implementer when a trusted active Work Packet "
        "authorizes the exact scope. " + atlas_legacy_gate
    )
    text = text.replace(legacy_gate, "")
    text = text.replace(
        "ChatGPT Chat is the default implementer for this repository when the authenticated active Work Packet "
        "authorizes the exact repository/worktree/branch/scope.", ""
    )
    text = text.replace("## ChatGPT implementation and audit contract", "## Implementation and audit contract")
    text = text.replace("ChatGPT Chat performs implementation, deterministic testing, and terminal audit.",
                        "The selected runtime performs implementation, deterministic testing, and terminal audit.")
    for sentence in (
        "When resuming a workstream, resolve this repository first, load only its single matching active AI Work Packet, verify actual branch/HEAD/state, and continue from the coherent Next Action.",
        "When explicitly resuming work, resolve this repository and branch first, load exactly one matching active repository-scoped AI Work Packet, verify actual HEAD/dirty/PR/CI state, and continue only from its Next Action.",
    ):
        text = text.replace(sentence, "When resuming, reconcile current owner intent, priority, dependencies and branch context; select eligible work and verify actual repository state before acting.")
    # Match complete known sentences; a custom suffix is independent policy.
    # Unknown variants stay intact rather than losing project-specific limits.
    legacy_watch_prefix = (
        "- Evaluate one bounded coordinator watch with `python3 tools/coordinator_watch.py evaluate "
        "--facts <facts.json> --watch-state <state.json>`. The evaluator is pure: one re-entry result, "
        "no subprocess, network, GitHub mutation, merge, or "
    )
    legacy_lines = {
        '- Do not spend coding-agent model time polling CI, review, or another machine-observable external wait. Persist concise waiting state and yield to coordinator/automation for re-entry.': '- Preserve machine-observable wait state and continue independent authorized work; use a watcher when useful.',
        "- Reconcile one Work Packet's next action with `python3 tools/coordinator.py plan --facts <facts.json>`. The planner is pure: one bounded decision, no worker launch, GitHub mutation, merge, notification send, or session stop.": '',
        '- Run one coordinator watch host pass with `python3 tools/coordinator_watch_host.py run-once --request <request.json>`. The host acquires one lock, calls the watch evaluator, and delivers at most one already-authorized typed action after a fresh reconciliation read. It does not accept caller commands or URLs, mint authority, merge, stop sessions, or busy-loop.': '',
        '- Do not keep a coding-agent session alive polling CI/review/external waits; persist concise state and yield to coordinator/automation.': '- Preserve machine-observable wait state and continue independent authorized work; use a watcher when useful.',
    }
    lines = []
    for line in text.splitlines():
        # Exact known legacy line only; preserve custom suffixes and local
        # privilege/release rules for explicit product-owner review.
        if line in (atlas_legacy_gate, atlas_legacy_numbered_rule):
            continue
        if line.startswith(legacy_watch_prefix):
            remainder = line[len(legacy_watch_prefix):]
            marker = " send."
            marker_at = remainder.find(marker)
            if marker_at > 0:
                suffix = remainder[marker_at + len(marker):].strip()
                line = ("- " + suffix) if suffix else None
        if line is None:
            continue
        for legacy_line, replacement in legacy_lines.items():
            if line == legacy_line or line.startswith(legacy_line + " "):
                suffix = line[len(legacy_line):].strip()
                line = replacement or None
                if suffix:
                    line = (replacement + " " if replacement else "- ") + suffix
                break
        if line is None:
            continue
        if line == "- If mandatory engineering context is missing or contradictory, fail closed instead of guessing.":
            line = "- Repair missing or contradictory coordination context within current owner scope; otherwise block only the affected action with concrete evidence."
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def plan_execution_policy_sync(root: Path) -> str | None:
    """Synchronize canonical execution policy and remove known retired agent rules.

    Product-specific rules remain untouched. Unknown retired-runtime text fails
    closed rather than being guessed away.
    """
    path = root / "AGENTS.md"
    if not path.is_file():
        return None
    original = path.read_text(encoding="utf-8")
    previous_templates = prior_agents_templates(root)
    current_template = (CANONICAL / "templates/AGENTS.md").read_text(encoding="utf-8")
    source = original
    # Replace only a complete, byte-identical old managed block. Added product
    # sections remain untouched; customized/interleaved documents use line sync.
    for previous_template in previous_templates:
        if source.count(previous_template) == 1:
            source = source.replace(previous_template, current_template, 1)
            break
    cleaned = rewrite_legacy_coordination_rules(rewrite_retired_agent_rules(source))
    known_managed_lines = {
        line for template in previous_templates
        for line in rewrite_legacy_coordination_rules(rewrite_retired_agent_rules(template)).splitlines()
        if line.strip()
    }
    # Past upgrades mixed old template body with newer managed policy lines.
    # Compact only when every nonblank line has known canonical provenance.
    # Even one unknown/custom line prevents this whole-document replacement.
    if known_managed_lines and all(
        not line.strip() or line in known_managed_lines for line in cleaned.splitlines()
    ):
        cleaned = current_template
    if retired_agent_rules_present(cleaned):
        raise SystemExit(
            "FAIL AGENTS.md contains unrecognized retired runtime rules; review manually"
        )

    (
        canonical_profile,
        canonical_execution,
        canonical_supervisor,
        canonical_next_chat_bootstrap,
        canonical_verified_next_chat_resume,
        canonical_external_write,
    ) = canonical_managed_policy_lines()
    selected_profile = _selected_profile()
    legacy_profile_markers = tuple(selected_profile["policy_migration"]["legacy_execution_profile_markers"])
    legacy_write_markers = tuple(selected_profile["policy_migration"]["legacy_external_write_markers"])
    lines = cleaned.splitlines()
    specs = (
        (
            "execution-profile",
            canonical_profile,
            (EXECUTION_PROFILE_MARKER,) + legacy_profile_markers,
        ),
        (
            "continuous-execution",
            canonical_execution,
            (EXECUTION_POLICY_MARKER,),
        ),
        (
            "product-supervisor-boundary",
            canonical_supervisor,
            (SUPERVISOR_POLICY_MARKER,),
        ),
        (
            "next-chat-bootstrap",
            canonical_next_chat_bootstrap,
            (NEXT_CHAT_BOOTSTRAP_POLICY_MARKER,),
        ),
        (
            "verified-next-chat-resume",
            canonical_verified_next_chat_resume,
            (VERIFIED_NEXT_CHAT_RESUME_POLICY_MARKER,),
        ),
        (
            "external-write-scope",
            canonical_external_write,
            (EXTERNAL_WRITE_POLICY_MARKER,) + legacy_write_markers,
        ),
        ("user-gate", canonical_user_gate_policy_line(), (USER_GATE_POLICY_MARKER,)),
    )
    exact_managed_labels = {
        "next-chat-bootstrap",
        "verified-next-chat-resume",
        "user-gate",
    }
    missing: list[str] = []
    for label, canonical, markers in specs:
        indexes = [
            i
            for i, line in enumerate(lines)
            if any(line.startswith(marker) for marker in markers)
        ]
        if len(indexes) > 1:
            raise SystemExit(f"FAIL AGENTS.md contains duplicate managed {label} policy lines")
        if indexes:
            index = indexes[0]
            current = lines[index]
            known_previous = {
                line for template in previous_templates for line in template.splitlines()
                if any(line.startswith(marker) for marker in markers)
            }
            if label in exact_managed_labels and current != canonical and current not in known_previous:
                raise SystemExit(
                    f"FAIL AGENTS.md contains customized managed {label} policy line; review manually"
                )
            suffix = ""
            if label not in exact_managed_labels and current != canonical and current not in known_previous:
                # Preserve additive local constraints on a known managed line.
                for known in sorted({canonical, *known_previous}, key=len, reverse=True):
                    if current.startswith(known + " "):
                        suffix = current[len(known):].strip()
                        break
                if not suffix and current.startswith(markers[0]):
                    raise SystemExit(
                        f"FAIL AGENTS.md contains customized managed {label} policy line; review manually"
                    )
            lines[index] = canonical + (("\n- " + suffix) if suffix else "")
        else:
            missing.append(canonical)

    if missing:
        heading_indexes = [i for i, line in enumerate(lines) if line == EXECUTION_RULES_HEADING]
        if len(heading_indexes) != 1:
            raise SystemExit(
                "FAIL AGENTS.md must contain exactly one '## Execution rules' heading "
                "before managed execution policy synchronization"
            )
        insert_at = heading_indexes[0] + 1
        if insert_at < len(lines) and lines[insert_at] == "":
            insert_at += 1
        for canonical in reversed(missing):
            lines.insert(insert_at, canonical)
    rewritten = "\n".join(lines)
    if original.endswith("\n"):
        rewritten += "\n"
    return None if rewritten == original else rewritten


def apply_execution_policy_sync(root: Path, planned_text: str | None) -> bool:
    if planned_text is None:
        return False
    (root / "AGENTS.md").write_text(planned_text, encoding="utf-8")
    return True


def remove_retired_agent_artifacts(root: Path) -> list[str]:
    removed: list[str] = []
    for rel in retired_artifact_paths(_selected_profile()):
        path = root / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(rel)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(rel)
        elif path.exists():
            path.unlink()
            removed.append(rel)
    return removed


# Prefer committed candidate/base diffs when ENGINEERING_BASE_REF is provided by shared CI.
WHITESPACE_CHECK_COMMAND = (
    'bash -lc \'if [ -n "${ENGINEERING_BASE_REF:-}" ]; then '
    'git diff --check "${ENGINEERING_BASE_REF}...HEAD"; '
    "elif git rev-parse --verify --quiet origin/main >/dev/null; then "
    "git diff --check origin/main...HEAD; "
    "elif git rev-parse --verify --quiet main >/dev/null; then "
    "git diff --check main...HEAD; "
    "elif git rev-parse --verify --quiet HEAD^ >/dev/null; then "
    "git diff --check HEAD^...HEAD; "
    "else git diff --check; fi'"
)


def run_git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def canonical_version() -> str:
    return (CANONICAL / "VERSION").read_text(encoding="utf-8").strip()


def canonical_baseline(explicit: str) -> str:
    if explicit:
        baseline = explicit.strip()
    else:
        baseline = run_git(CANONICAL, "rev-parse", "HEAD")
    if not FULL_SHA_RE.fullmatch(baseline):
        raise SystemExit(
            "FAIL canonical baseline SHA unavailable; pass --baseline-sha with a 40-character commit SHA"
        )
    return baseline


def git_state(root: Path) -> dict[str, str | bool]:
    top = run_git(root, "rev-parse", "--show-toplevel")
    return {
        "is_git_repo": bool(top),
        "root": top or str(root),
        "origin": run_git(root, "remote", "get-url", "origin"),
        "branch": run_git(root, "branch", "--show-current"),
        "head": run_git(root, "rev-parse", "HEAD"),
        "dirty": bool(run_git(root, "status", "--porcelain")),
    }


def detect_project_type(root: Path) -> str:
    types: list[str] = []
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        types.append("python")
    if (root / "package.json").is_file():
        types.append("node")
    if (root / "go.mod").is_file():
        types.append("go")
    if (root / "Cargo.toml").is_file():
        types.append("rust")
    if (root / "pom.xml").is_file() or (root / "mvnw").is_file():
        types.append("maven")
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file() or (root / "gradlew").is_file():
        types.append("gradle")
    if len(types) > 1:
        return "mixed-" + "-".join(types)
    return types[0] if types else "generic"


def node_package_manager(root: Path) -> str:
    """Prefer Bun when Bun lockfiles are present, then pnpm/yarn/npm."""
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def node_test_command(root: Path) -> str:
    package = root / "package.json"
    if not package.is_file():
        return ""
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except Exception:
        return ""
    test = str(((data.get("scripts") or {}).get("test")) or "").strip()
    manager = node_package_manager(root)
    # Bun's native test runner does not require package.json scripts.test.
    if manager == "bun":
        return "bun test"
    if not test or "no test specified" in test.lower():
        return ""
    if manager == "pnpm":
        return "pnpm test"
    if manager == "yarn":
        return "yarn test"
    return "npm test"


def package_script_command(root: Path, script: str) -> str:
    package = root / "package.json"
    if not package.is_file():
        return ""
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except Exception:
        return ""
    scripts = data.get("scripts") or {}
    if not str(scripts.get(script) or "").strip():
        return ""
    manager = node_package_manager(root)
    if manager == "bun":
        return f"bun run {script}"
    if manager == "pnpm":
        return f"pnpm {script}"
    if manager == "yarn":
        return f"yarn {script}"
    return f"npm run {script}"


def make_target(root: Path, target: str) -> str:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return ""
    text = makefile.read_text(encoding="utf-8", errors="replace")
    if re.search(rf"(?m)^{re.escape(target)}\s*:", text):
        return f"make {target}"
    return ""


def discover_quality_commands(root: Path) -> dict[str, str]:
    commands = {
        "build": package_script_command(root, "build") or make_target(root, "build"),
        "lint": package_script_command(root, "lint") or make_target(root, "lint"),
        "typecheck": (
            package_script_command(root, "typecheck")
            or package_script_command(root, "type-check")
            or make_target(root, "typecheck")
        ),
    }
    return {name: command for name, command in commands.items() if command}


def discover_domain_map(root: Path) -> dict[str, list[str]]:
    source_candidates = (
        "server", "client", "agent", "frontend", "backend", "api", "cli",
        "web", "cmd", "pkg", "internal", "src", "lib", "app"
    )
    present = [name for name in source_candidates if (root / name).is_dir()]
    specific = [name for name in present if name not in {"src", "lib", "app"}]
    selected = specific if len(specific) >= 2 else []

    if not selected:
        return {"core": source_patterns(root)}

    mapping: dict[str, list[str]] = {name: [f"{name}/**"] for name in selected}
    shared_patterns = [
        f"{name}/**"
        for name in ("src", "lib", "app", "tests", "test")
        if (root / name).exists()
    ]
    if shared_patterns:
        mapping["shared"] = shared_patterns
    return mapping


def discover_operations_signals(root: Path) -> list[str]:
    markers = (
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "helm", "charts", "k8s", "kubernetes", "deploy", "deployment",
        "terraform", "ansible", "systemd", "packaging", "installer"
    )
    return [name for name in markers if (root / name).exists()]


def discover_test_commands(root: Path) -> list[str]:
    commands: list[str] = []

    explicit_runners = (
        ("tests/run-all.sh", "bash tests/run-all.sh"),
        ("tests/run.sh", "bash tests/run.sh"),
        ("test/run-all.sh", "bash test/run-all.sh"),
    )
    for rel, command in explicit_runners:
        if (root / rel).is_file():
            commands.append(command)

    if (root / "go.mod").is_file():
        commands.append("go test ./...")
    if (root / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (root / "mvnw").is_file():
        commands.append("./mvnw test")
    elif (root / "pom.xml").is_file():
        commands.append("mvn test")
    if (root / "gradlew").is_file():
        commands.append("./gradlew test")
    elif (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        commands.append("gradle test")

    node = node_test_command(root)
    if node:
        commands.append(node)

    python_markers = (
        root / "pytest.ini",
        root / "pyproject.toml",
        root / "setup.cfg",
    )
    if (root / "tests").is_dir() and any(p.exists() for p in python_markers):
        commands.append("python -m pytest -q")

    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for command in commands:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique


def suggest_setup_command(root: Path, test_command: str) -> str:
    if test_command == "bun test":
        return "bun install --frozen-lockfile"
    if test_command in {"npm test", "pnpm test", "yarn test"}:
        if test_command == "pnpm test":
            return "corepack enable && pnpm install --frozen-lockfile"
        if test_command == "yarn test":
            return "corepack enable && yarn install --immutable"
        if (root / "package-lock.json").is_file():
            return "npm ci"
        return "npm install"

    if test_command == "python -m pytest -q":
        commands: list[str] = []
        if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file() or (root / "setup.cfg").is_file():
            commands.append("python -m pip install -e .")
        for rel in ("requirements-dev.txt", "requirements-test.txt", "requirements.txt"):
            if (root / rel).is_file():
                commands.append(f"python -m pip install -r {rel}")
                break
        commands.append("python -m pip install pytest")
        return " && ".join(commands)

    return ""


def source_patterns(root: Path) -> list[str]:
    candidates = ("src", "lib", "app", "cmd", "pkg", "internal", "server", "client", "tests", "test")
    patterns = [f"{name}/**" for name in candidates if (root / name).exists()]
    if not patterns:
        patterns = ["**"]
    return patterns


def rule_surfaces(root: Path) -> list[str]:
    found: list[str] = []
    for rel in RULE_SURFACES:
        path = root / rel
        if path.is_file():
            found.append(rel)
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    found.append(str(item.relative_to(root)))
    return found


def review_required_rules(root: Path, rules: list[str]) -> list[str]:
    exact_generated = {
        "AGENTS.md": CANONICAL / "templates" / "AGENTS.md",
    }
    required: list[str] = []
    for rel in rules:
        source = exact_generated.get(rel)
        target = root / rel
        if source and source.is_file() and target.is_file():
            if source.read_text(encoding="utf-8") == target.read_text(encoding="utf-8", errors="replace"):
                continue
        required.append(rel)
    return required


def existing_ci(root: Path) -> list[str]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    return [
        str(path.relative_to(root))
        for path in sorted(workflow_dir.iterdir())
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    ]


def github_repo_slug(root: Path) -> str:
    origin = run_git(root, "remote", "get-url", "origin")
    if not origin:
        return ""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", origin)
    return match.group(1) if match else ""


def gh_api_json(endpoint: str):
    try:
        output = subprocess.check_output(
            ["gh", "api", endpoint],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return json.loads(output)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return None


def detect_merge_gate_status(root: Path) -> str:
    slug = github_repo_slug(root)
    if not slug:
        return "unknown"

    repo = gh_api_json(f"repos/{slug}")
    rulesets = gh_api_json(f"repos/{slug}/rulesets?per_page=100")
    if not isinstance(repo, dict) or not isinstance(rulesets, list):
        return "unknown"

    default_branch = str(repo.get("default_branch") or "")
    if not default_branch:
        return "unknown"

    candidates = {"~ALL", "~DEFAULT_BRANCH", default_branch, f"refs/heads/{default_branch}"}
    has_pr_gate = False
    has_required_checks = False

    for summary in rulesets:
        if summary.get("target") != "branch" or summary.get("enforcement") != "active":
            continue
        ruleset_id = summary.get("id")
        if ruleset_id is None:
            continue
        detail = gh_api_json(f"repos/{slug}/rulesets/{ruleset_id}")
        if not isinstance(detail, dict):
            continue

        ref = ((detail.get("conditions") or {}).get("ref_name") or {})
        includes = set(ref.get("include") or [])
        excludes = set(ref.get("exclude") or [])
        if includes and not (includes & candidates):
            continue
        if excludes & candidates:
            continue

        for rule in detail.get("rules") or []:
            if rule.get("type") == "pull_request":
                has_pr_gate = True
            elif rule.get("type") == "required_status_checks":
                checks = ((rule.get("parameters") or {}).get("required_status_checks") or [])
                if checks:
                    has_required_checks = True

    return "verified" if has_pr_gate and has_required_checks else "advisory"


def inventory(root: Path) -> dict[str, object]:
    test_candidates = discover_test_commands(root)
    setup_suggestion = suggest_setup_command(root, test_candidates[0]) if len(test_candidates) == 1 else ""
    return {
        "git": git_state(root),
        "project_type": detect_project_type(root),
        "test_candidates": test_candidates,
        "quality_candidates": discover_quality_commands(root),
        "setup_suggestion": setup_suggestion,
        "domain_candidates": discover_domain_map(root),
        "operations_signals": discover_operations_signals(root),
        "source_patterns": source_patterns(root),
        "existing_rule_surfaces": rule_surfaces(root),
        "existing_ci": existing_ci(root),
        "existing_adoption_files": [rel for rel in REQUIRED_MANAGED if (root / rel).is_file()],
    }


def print_inventory(data: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    git = data["git"]
    assert isinstance(git, dict)
    print(f"TARGET_ROOT={git.get('root', '')}")
    print(f"TARGET_ORIGIN={git.get('origin', '') or '<none>'}")
    print(f"TARGET_BRANCH={git.get('branch', '') or '<none>'}")
    print(f"TARGET_HEAD={git.get('head', '') or '<none>'}")
    print(f"WORKTREE_DIRTY={'YES' if git.get('dirty') else 'NO'}")
    print(f"PROJECT_TYPE={data['project_type']}")
    tests = data["test_candidates"]
    rules = data["existing_rule_surfaces"]
    ci = data["existing_ci"]
    print("TEST_CANDIDATES=" + (" | ".join(tests) if tests else "<none>"))
    quality = data.get("quality_candidates") or {}
    print("QUALITY_CANDIDATES=" + (json.dumps(quality, sort_keys=True) if quality else "<none>"))
    print("DOMAIN_CANDIDATES=" + json.dumps(data.get("domain_candidates") or {}, sort_keys=True))
    operations = data.get("operations_signals") or []
    print("OPERATIONS_SIGNALS=" + (",".join(operations) if operations else "<none>"))
    print("SETUP_SUGGESTION=" + (str(data.get("setup_suggestion") or "") or "<none>"))
    print("RULE_SURFACES=" + (",".join(rules) if rules else "<none>"))
    print("CI_WORKFLOWS=" + (",".join(ci) if ci else "<none>"))
    print("ADOPTION_AUDIT=PASS")


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def require_repository_relative_contract(root: Path, value: str, label: str) -> Path:
    raw = value.strip()
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(
            f"FAIL user-facing {label} contract path must be repository-relative and stay inside repository: {raw}"
        )
    try:
        candidate = (root / relative).resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise SystemExit(
            f"FAIL user-facing {label} contract path must be repository-relative and stay inside repository: {raw}"
        )
    if not candidate.is_file():
        raise SystemExit(f"FAIL user-facing {label} contract missing: {raw}")
    return candidate


def parse_domain_tests(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"FAIL invalid --domain-test {value!r}; expected domain=command")
        domain, command = value.split("=", 1)
        domain = domain.strip()
        command = command.strip()
        if not domain or not command:
            raise SystemExit(f"FAIL invalid --domain-test {value!r}; expected domain=command")
        result[domain] = command
    return result


def project_yaml(
    root: Path,
    version: str,
    baseline: str,
    ci_mode: str,
    native_ci_workflows: list[str],
    merge_gate_status: str,
    project_type: str,
    maturity: str,
    user_facing: bool,
    primary_user_surface: str,
    domains: list[str],
    platform: str,
    operations_mode: str,
    persistent_state: bool,
    runbook_paths: list[str],
    health_command: str,
    backup_command: str,
    restore_test_command: str,
    upgrade_command: str,
    rollback_command: str,
) -> str:
    production = operations_mode == "production"
    lines = [
        "engineering_system:",
        f"  version: {yaml_scalar(version)}",
        f"  policy_epoch: {POLICY_EPOCH}",
        f"  governance_epoch: {GOVERNANCE_EPOCH}",
        "  mode: adopted",
        f"  baseline: {yaml_scalar(baseline)}",
        f"  ci_mode: {yaml_scalar(ci_mode)}",
        "  native_ci_workflows:",
    ]
    if native_ci_workflows:
        lines.extend(f"    - {yaml_scalar(path)}" for path in native_ci_workflows)
    else:
        lines[-1] = "  native_ci_workflows: []"
    lines.extend(
        [
            f"  merge_gate_status: {yaml_scalar(merge_gate_status)}",
            "",
            "project:",
            f"  name: {yaml_scalar(root.name)}",
            f"  type: {yaml_scalar(project_type)}",
            f"  maturity: {yaml_scalar(maturity)}",
            f"  user_facing: {'true' if user_facing else 'false'}",
            f"  primary_user_surface: {yaml_scalar(primary_user_surface if user_facing else 'none')}",
            "",
            "domains:",
        ]
    )
    lines.extend(f"  - {yaml_scalar(domain)}" for domain in domains)
    lines.extend(
        [
            "",
            "platforms:",
            f"  - {yaml_scalar(platform)}",
            "",
            "operations:",
            f"  production_oriented: {'true' if production else 'false'}",
            f"  runbook_required: {'true' if production else 'false'}",
            f"  incident_response_required: {'true' if production else 'false'}",
            f"  persistent_state: {'true' if persistent_state else 'false'}",
            "  runbook_paths:",
        ]
    )
    if runbook_paths:
        lines.extend(f"    - {yaml_scalar(path)}" for path in runbook_paths)
    else:
        lines[-1] = "  runbook_paths: []"
    lines.extend(
        [
            f"  health_command: {yaml_scalar(health_command)}",
            f"  backup_command: {yaml_scalar(backup_command)}",
            f"  restore_test_command: {yaml_scalar(restore_test_command)}",
            f"  upgrade_command: {yaml_scalar(upgrade_command)}",
            f"  rollback_command: {yaml_scalar(rollback_command)}",
            "",
        ]
    )
    return "\n".join(lines)


def tests_yaml(
    domain_map: dict[str, list[str]],
    setup_command: str,
    test_command: str,
    domain_tests: dict[str, str],
    quality_commands: dict[str, str],
    platform: str,
) -> str:
    domains = list(domain_map)
    lines = ["version: 1", "", f"setup_command: {yaml_scalar(setup_command)}", "", "paths:"]
    for domain, patterns in domain_map.items():
        for pattern in patterns:
            lines.extend(
                [
                    f"  {yaml_scalar(pattern)}:",
                    "    domains:",
                    f"      - {yaml_scalar(domain)}",
                ]
            )

    lines.extend(["", "scenarios:"])
    if domain_tests:
        for index, (domain, command) in enumerate(sorted(domain_tests.items()), start=1):
            lines.extend(
                [
                    f"  - id: ADOPTED-DOMAIN-{index:03d}",
                    "    cost: medium",
                    "    estimated_seconds: 120",
                    "    timeout_seconds: 600",
                    "    agent_default: true",
                    "    scope: integration",
                    f"    name: {domain} affected tests",
                    "    level: integration",
                    "    domains:",
                    f"      - {yaml_scalar(domain)}",
                    "    triggers:",
                    "      - affected",
                    "    platforms:",
                    f"      - {yaml_scalar(platform)}",
                    f"    command: {yaml_scalar(command)}",
                    "    invariants:",
                    f"      - {yaml_scalar(domain + ' behavior remains green')}",
                    "    release_gate: true",
                    "",
                ]
            )
    elif test_command:
        lines.extend(
            [
                "  - id: ADOPTED-TEST-001",
                "    cost: medium",
                "    estimated_seconds: 180",
                "    timeout_seconds: 600",
                "    agent_default: true",
                "    scope: integration",
                "    name: Project-native affected tests",
                "    level: integration",
                "    domains:",
            ]
        )
        lines.extend(f"      - {yaml_scalar(domain)}" for domain in domains)
        lines.extend(
            [
                "    triggers:",
                "      - affected",
                "    platforms:",
                f"      - {yaml_scalar(platform)}",
                f"    command: {yaml_scalar(test_command)}",
                "    invariants:",
                '      - "project-native tests remain green for affected changes"',
                "    release_gate: true",
                "",
            ]
        )

    for name in ("lint", "typecheck", "build"):
        command = quality_commands.get(name, "")
        if not command:
            continue
        scenario_id = f"ADOPTED-{name.upper()}-001"
        trigger = "pr" if name in {"lint", "typecheck"} else "affected"
        lines.extend(
            [
                f"  - id: {scenario_id}",
                "    cost: cheap" if name in {"lint", "typecheck"} else "    cost: medium",
                "    estimated_seconds: 60" if name in {"lint", "typecheck"} else "    estimated_seconds: 180",
                "    timeout_seconds: 180" if name in {"lint", "typecheck"} else "    timeout_seconds: 600",
                "    agent_default: true",
                "    scope: static" if name in {"lint", "typecheck"} else "    scope: component",
                f"    name: Project {name}",
                "    level: static" if name in {"lint", "typecheck"} else "    level: component",
                "    domains:",
            ]
        )
        lines.extend(f"      - {yaml_scalar(domain)}" for domain in domains)
        lines.extend(
            [
                "    triggers:",
                f"      - {trigger}",
                "    platforms:",
                f"      - {yaml_scalar(platform)}",
                f"    command: {yaml_scalar(command)}",
                "    invariants:",
                f"      - {yaml_scalar('project ' + name + ' remains green')}",
                "    release_gate: true",
                "",
            ]
        )

    lines.extend(
        [
            "  - id: ADOPTED-STATIC-001",
            "    cost: cheap",
            "    estimated_seconds: 2",
            "    timeout_seconds: 30",
            "    agent_default: true",
            "    scope: static",
            "    name: Git whitespace validation",
            "    level: static",
            "    domains:",
        ]
    )
    lines.extend(f"      - {yaml_scalar(domain)}" for domain in domains)
    lines.extend(
        [
            "    triggers:",
            "      - pr",
            "      - preflight",
            "      - release",
            "    platforms:",
            f"      - {yaml_scalar(platform)}",
            f"    command: {yaml_scalar(WHITESPACE_CHECK_COMMAND)}",
            "    invariants:",
            '      - "committed candidate/base diff has no whitespace errors"',
            "    release_gate: true",
            "",
        ]
    )
    return "\n".join(lines)


def release_yaml(
    release_setup_command: str,
    preflight_command: str,
    release_command: str,
    artifact_hash_command: str,
    provenance_command: str,
    sbom_command: str,
    operations_mode: str,
    release_execution_context: str,
    operational_e2e_command: str,
    full_e2e_passes: int,
    public_smoke_command: str,
    user_facing: bool,
    primary_user_surface: str,
    surface_reconciliation_contract: str,
    full_user_e2e_contract: str,
) -> str:
    production = operations_mode == "production"
    lines = [
        "version: 1",
        "",
        "exact_head_required: true",
        f"artifact_hash_required: {'true' if artifact_hash_command else 'false'}",
        f"provenance_required: {'true' if provenance_command else 'false'}",
        f"sbom_required: {'true' if sbom_command else 'false'}",
        f"execution_context: {yaml_scalar(release_execution_context)}",
        f"setup_command: {yaml_scalar(release_setup_command)}",
        f"preflight_required: {'true' if preflight_command else 'false'}",
        f"preflight_command: {yaml_scalar(preflight_command)}",
        f"qualification_command: {yaml_scalar(release_command)}",
        f"artifact_hash_command: {yaml_scalar(artifact_hash_command)}",
        f"provenance_command: {yaml_scalar(provenance_command)}",
        f"sbom_command: {yaml_scalar(sbom_command)}",
        f"operational_e2e_required: {'true' if production else 'false'}",
        f"operational_e2e_command: {yaml_scalar(operational_e2e_command)}",
        f"full_e2e_passes: {full_e2e_passes if production else 0}",
        f"public_smoke_required: {'true' if production else 'false'}",
        f"public_smoke_command: {yaml_scalar(public_smoke_command)}",
        f"human_equivalent_user_tests_required: {'true' if user_facing else 'false'}",
    ]
    if user_facing:
        browser_required = primary_user_surface in {"browser", "mixed"}
        lines.extend([
            "human_equivalent_user_tests:",
            "  contract_version: 2",
            "  executor: EXECUTION_PROFILE",
            "  direct_persona_execution_required: true",
            "  canonical_contract_read_before_execution_required: true",
            "  complete_rerun_after_remediation_required: true",
            "  wrapper_user_substitution_forbidden: true",
            "  contract_review_attestation_version: 1",
            "  actual_user_surface_required: true",
            f"  primary_user_surface: {yaml_scalar(primary_user_surface)}",
            f"  actual_browser_process_required: {'true' if browser_required else 'false'}",
            "  same_candidate_required: true",
            "  finding_accumulation_before_remediation: true",
            "  same_head_quality_closure_required: true",
            "  candidate_freeze_after_quality_closure: true",
            "  ci_contract_validation_only: true",
            "  evidence_validator: tools/user_acceptance_contract.py",
            "  surface_reconciliation:",
            "    mandatory: true",
            f"    contract: {yaml_scalar(surface_reconciliation_contract)}",
            "    minimum_passes: 1",
            "  full_user_e2e:",
            "    mandatory: true",
            f"    contract: {yaml_scalar(full_user_e2e_contract)}",
            "    minimum_passes: 1",
        ])
    lines.extend([
        "",
        "blockers:",
        "  p0: true",
        "  p1: true",
        "  user_blocking_p2: true",
        "",
    ])
    return "\n".join(lines)

def engineering_workflow(baseline: str, ci_mode: str) -> str:
    lines = [
        "name: Engineering System",
        "",
        "on:",
        "  pull_request:",
        "  pull_request_target:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "jobs:",
        "  governance-floor:",
        "    if: github.event_name == 'pull_request_target'",
        f"    uses: datarelay-labs/engineering-system/.github/workflows/governance-floor.yml@{baseline}",
        "    with:",
        "      base_sha: ${{ github.event.pull_request.base.sha }}",
        "      head_sha: ${{ github.event.pull_request.head.sha }}",
        "",
        "  adoption-compliance:",
        "    if: github.event_name == 'pull_request'",
        f"    uses: datarelay-labs/engineering-system/.github/workflows/adoption-compliance.yml@{baseline}",
    ]
    lines.extend(
        [
            "",
            "  enforcement-reconcile:",
            "    if: github.event_name == 'pull_request'",
            f"    uses: datarelay-labs/engineering-system/.github/workflows/enforcement-check.yml@{baseline}",
        ]
    )
    if ci_mode == "shared":
        lines.extend(
            [
                "",
                "  affected-tests:",
                "    if: github.event_name == 'pull_request'",
                f"    uses: datarelay-labs/engineering-system/.github/workflows/affected-tests.yml@{baseline}",
                "    with:",
                "      manifest_path: .engineering/tests.yaml",
                "      trigger: pr",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def release_workflow(baseline: str) -> str:
    expression = "$" + "{{ inputs.expected_sha }}"
    phase_expression = "$" + "{{ inputs.phase }}"
    return "\n".join(
        [
            "name: Engineering Release Contract",
            "",
            "on:",
            "  workflow_dispatch:",
            "    inputs:",
            "      expected_sha:",
            "        description: Exact candidate SHA",
            "        required: true",
            "        type: string",
            "      phase:",
            "        description: Release contract phase",
            "        required: true",
            "        type: choice",
            "        default: qualify",
            "        options:",
            "          - qualify",
            "          - post-release",
            "",
            "permissions:",
            "  contents: read",
            "",
            "jobs:",
            "  release-contract:",
            f"    uses: datarelay-labs/engineering-system/.github/workflows/release-contract.yml@{baseline}",
            "    with:",
            f"      expected_sha: {expression}",
            f"      phase: {phase_expression}",
            "      profile_path: .engineering/release.yaml",
            "",
        ]
    )


def ensure_runtime_contract_compatible(root: Path) -> None:
    """Reject an incompatible helper or schema before adoption writes any files.

    A missing path is installed later. A byte-identical canonical copy is preserved.
    `.engineering/runtime.yaml` is not consulted and is never created.
    """
    for rel in RUNTIME_CONTRACT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_knowledge_contract_compatible(root: Path) -> None:
    """Reject an incompatible helper or schema before adoption writes any files.

    A missing path is installed later. A byte-identical canonical copy is preserved.
    `.engineering/knowledge.yaml` is not consulted and is never created.
    """
    for rel in KNOWLEDGE_CONTRACT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_agent_runtime_compatible(root: Path) -> None:
    """Reject custom managed runtime helpers before adoption writes."""
    for rel in AGENT_RUNTIME_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_implementation_preflight_compatible(root: Path) -> None:
    # Reject a custom implementation-preflight helper before adoption writes.
    for rel in IMPLEMENTATION_PREFLIGHT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_terminal_completion_notify_compatible(root: Path) -> None:
    """Reject a custom terminal completion notifier before adoption writes any files."""
    for rel in TERMINAL_COMPLETION_NOTIFY_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_execution_profile_compatible(root: Path) -> None:
    """Reject custom execution-profile managed surfaces before adoption writes."""
    for rel in EXECUTION_PROFILE_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_context_epoch_compatible(root: Path) -> None:
    """Reject a custom context-epoch helper before adoption writes any files."""
    for rel in CONTEXT_EPOCH_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_engineering_context_compatible(root: Path) -> None:
    """Reject a custom engineering-context helper before adoption writes any files."""
    for rel in ENGINEERING_CONTEXT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_governance_floor_compatible(root: Path) -> None:
    """Reject a custom governance-floor helper before adoption writes any files."""
    for rel in GOVERNANCE_FLOOR_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_verification_contract_compatible(root: Path) -> None:
    """Reject an incompatible helper or schema before adoption writes any files.

    A missing path is installed later. A byte-identical canonical copy is preserved.
    `.engineering/verification.yaml` is not consulted and is never created.
    """
    for rel in VERIFICATION_CONTRACT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_user_acceptance_compatible(root: Path) -> None:
    """Reject incompatible managed user-acceptance validator/schema before writes."""
    for rel in USER_ACCEPTANCE_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def ensure_skills_contract_compatible(root: Path) -> None:
    """Reject an incompatible helper or schema before adoption writes any files.

    A missing path is installed later. A byte-identical canonical copy is preserved.
    `.engineering/skills.yaml` is not consulted and is never created.
    """
    for rel in SKILLS_CONTRACT_MANAGED:
        path = root / rel
        if not path.exists():
            continue
        canonical = (CANONICAL / rel).read_text(encoding="utf-8")
        if path.is_file() and path.read_text(encoding="utf-8") == canonical:
            continue
        raise SystemExit(
            f"FAIL {rel} contains local/custom changes; preserve/review them manually before adoption"
        )


def write_missing(root: Path, rel: str, content: str, written: list[str], skipped: list[str]) -> None:
    path = root / rel
    if path.exists():
        skipped.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(rel)


def ensure_no_existing_managed_upgrade(root: Path, version: str) -> None:
    project = root / ".engineering" / "project.yaml"
    if not project.is_file():
        return
    text = project.read_text(encoding="utf-8", errors="replace")
    if version not in text:
        raise SystemExit(
            "FAIL existing Engineering System adoption detected at another version; audit and upgrade it deliberately instead of using bootstrap apply"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit or bootstrap Engineering System adoption")
    parser.add_argument("--root", required=True)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--ack-rule-review", action="store_true")
    parser.add_argument("--allow-no-tests", action="store_true")
    parser.add_argument("--test-command", default="")
    parser.add_argument("--domain-test", action="append", default=[])
    parser.add_argument("--setup-command", default="")
    parser.add_argument("--build-command", default="")
    parser.add_argument("--lint-command", default="")
    parser.add_argument("--typecheck-command", default="")
    parser.add_argument("--release-command", default="")
    parser.add_argument("--release-setup-command", default="")
    parser.add_argument("--preflight-command", default="")
    parser.add_argument("--artifact-hash-command", default="")
    parser.add_argument("--provenance-command", default="")
    parser.add_argument("--sbom-command", default="")
    parser.add_argument("--release-execution-context", default="github-hosted", choices=("github-hosted", "protected-production"))
    parser.add_argument("--operational-e2e-command", default="")
    parser.add_argument("--public-smoke-command", default="")
    parser.add_argument("--full-e2e-passes", type=int, default=1)
    parser.add_argument("--baseline-sha", default="")
    parser.add_argument("--project-type", default="")
    parser.add_argument("--user-facing", action="store_true")
    parser.add_argument("--user-gate-contracts-reviewed", action="store_true")
    parser.add_argument("--primary-user-surface", default="none", choices=("none", "browser", "cli", "desktop", "mobile", "mixed", "other"))
    parser.add_argument("--surface-reconciliation-contract", default="")
    parser.add_argument("--full-user-e2e-contract", default="")
    parser.add_argument("--ci-mode", default="auto", choices=("auto", "shared", "native"))
    parser.add_argument("--native-ci-workflow", action="append", default=[])
    parser.add_argument("--merge-gate-status", default="auto", choices=("auto", "verified", "advisory", "unknown"))
    parser.add_argument("--maturity", default="development", choices=("experimental", "development", "production", "maintenance"))
    parser.add_argument("--operations-mode", default="auto", choices=("auto", "production", "nonproduction"))
    parser.add_argument("--persistent-state", action="store_true")
    parser.add_argument("--runbook-path", action="append", default=[])
    parser.add_argument("--health-command", default="")
    parser.add_argument("--backup-command", default="")
    parser.add_argument("--restore-test-command", default="")
    parser.add_argument("--upgrade-command", default="")
    parser.add_argument("--rollback-command", default="")
    parser.add_argument("--domain", default="")
    parser.add_argument("--platform", default="linux")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"FAIL target root does not exist: {root}")

    if args.user_facing:
        if args.primary_user_surface == "none":
            raise SystemExit("FAIL --user-facing requires --primary-user-surface")
        if not args.surface_reconciliation_contract.strip() or not args.full_user_e2e_contract.strip():
            raise SystemExit("FAIL --user-facing requires both --surface-reconciliation-contract and --full-user-e2e-contract")
        for label, rel in (("surface reconciliation", args.surface_reconciliation_contract), ("Full User E2E", args.full_user_e2e_contract)):
            require_repository_relative_contract(root, rel, label)
        if args.apply and not args.user_gate_contracts_reviewed:
            raise SystemExit(
                "FAIL --user-facing apply requires --user-gate-contracts-reviewed after explicitly reviewing "
                "both repository-local user-gate contracts for contract-first execution, direct persona "
                "ownership, wrapper/script non-substitution, targeted affected convergence, and final "
                "complete confirmation-rerun semantics"
            )
    elif args.primary_user_surface != "none":
        raise SystemExit("FAIL --primary-user-surface requires --user-facing")

    data = inventory(root)
    if args.audit or not args.apply:
        print_inventory(data, args.json)
        if not args.apply:
            return 0

    git = data["git"]
    assert isinstance(git, dict)
    if not git.get("is_git_repo"):
        raise SystemExit("FAIL target must be a Git repository")
    if git.get("dirty") and not args.allow_dirty:
        raise SystemExit("FAIL target worktree is dirty; preserve unrelated work or pass --allow-dirty after explicit review")

    rules = list(data["existing_rule_surfaces"])
    preexisting_rules = review_required_rules(root, rules)
    if preexisting_rules and not args.ack_rule_review:
        print("RULE_REVIEW_REQUIRED=" + ",".join(preexisting_rules))
        raise SystemExit("FAIL classify existing rules before apply; rerun with --ack-rule-review after review")

    version = canonical_version()
    ensure_no_existing_managed_upgrade(root, version)
    baseline = canonical_baseline(args.baseline_sha)

    candidates = list(data["test_candidates"])
    test_command = args.test_command.strip()
    if not test_command:
        if len(candidates) == 1:
            test_command = candidates[0]
        elif len(candidates) > 1:
            print("TEST_CANDIDATES=" + " | ".join(candidates))
            raise SystemExit("FAIL multiple test commands discovered; pass --test-command explicitly")
        elif not args.allow_no_tests:
            raise SystemExit("FAIL no unambiguous test command found; pass --test-command or --allow-no-tests")

    domain_tests = parse_domain_tests(list(args.domain_test))
    if domain_tests and test_command:
        raise SystemExit("FAIL use either --test-command or --domain-test, not both")

    setup_command = args.setup_command.strip()
    if not setup_command and test_command:
        setup_command = suggest_setup_command(root, test_command)

    discovered_quality = dict(data.get("quality_candidates") or {})
    quality_commands = {
        "build": args.build_command.strip() or str(discovered_quality.get("build") or ""),
        "lint": args.lint_command.strip() or str(discovered_quality.get("lint") or ""),
        "typecheck": args.typecheck_command.strip() or str(discovered_quality.get("typecheck") or ""),
    }
    quality_commands = {name: command for name, command in quality_commands.items() if command}

    if args.full_e2e_passes < 0:
        raise SystemExit("FAIL --full-e2e-passes must be >= 0")

    project_type = args.project_type.strip() or str(data["project_type"])
    if args.domain.strip():
        domain_map = {args.domain.strip(): list(data["source_patterns"])}
    else:
        domain_map = dict(data.get("domain_candidates") or {"core": list(data["source_patterns"])})
    if domain_tests:
        unknown = sorted(set(domain_tests) - set(domain_map))
        if unknown:
            raise SystemExit("FAIL --domain-test references unknown domain(s): " + ",".join(unknown))
    domains = list(domain_map)

    operations_mode = args.operations_mode
    operations_signals = list(data.get("operations_signals") or [])
    if operations_mode == "auto":
        if args.maturity in {"production", "maintenance"}:
            operations_mode = "production"
        elif operations_signals:
            print("OPERATIONS_REVIEW_REQUIRED=" + ",".join(operations_signals))
            raise SystemExit("FAIL deployment/operations signals detected; rerun with --operations-mode production|nonproduction after review")
        else:
            operations_mode = "nonproduction"

    detected_merge_gate = detect_merge_gate_status(root)
    if args.merge_gate_status == "auto":
        merge_gate_status = detected_merge_gate
    else:
        merge_gate_status = args.merge_gate_status
        if detected_merge_gate != "unknown" and merge_gate_status != detected_merge_gate:
            raise SystemExit(
                "FAIL declared merge-gate status conflicts with live GitHub enforcement: "
                f"declared={merge_gate_status} observed={detected_merge_gate}"
            )

    runbook_paths = [item.strip() for item in args.runbook_path if item.strip()]
    health_command = args.health_command.strip()
    backup_command = args.backup_command.strip()
    restore_test_command = args.restore_test_command.strip()
    upgrade_command = args.upgrade_command.strip()
    rollback_command = args.rollback_command.strip()

    if operations_mode == "production":
        if not runbook_paths:
            raise SystemExit("FAIL production adoption requires at least one --runbook-path")
        missing_runbooks = [path for path in runbook_paths if not (root / path).is_file()]
        if missing_runbooks:
            raise SystemExit("FAIL production runbook path missing: " + ",".join(missing_runbooks))
        if not health_command:
            raise SystemExit("FAIL production adoption requires --health-command")
    if args.persistent_state and (not backup_command or not restore_test_command):
        raise SystemExit(
            "FAIL --persistent-state requires --backup-command and --restore-test-command"
        )

    operational_e2e_command = args.operational_e2e_command.strip()
    public_smoke_command = args.public_smoke_command.strip()
    if operations_mode == "production":
        if not operational_e2e_command:
            raise SystemExit("FAIL production adoption requires --operational-e2e-command")
        if args.full_e2e_passes < 1:
            raise SystemExit("FAIL production adoption requires --full-e2e-passes >= 1")
        if not public_smoke_command:
            raise SystemExit("FAIL production adoption requires --public-smoke-command")

    ci_mode = args.ci_mode
    existing_workflows = [
        item for item in list(data["existing_ci"])
        if item != ".github/workflows/engineering-system.yml"
    ]
    if ci_mode == "auto":
        if existing_workflows:
            print("CI_REVIEW_REQUIRED=" + ",".join(existing_workflows))
            raise SystemExit("FAIL existing CI detected; review equivalent gates and rerun with --ci-mode shared|native")
        ci_mode = "shared"

    native_ci_workflows = [item.strip() for item in args.native_ci_workflow if item.strip()]
    if ci_mode == "native":
        if not native_ci_workflows:
            if len(existing_workflows) == 1:
                native_ci_workflows = [existing_workflows[0]]
            elif len(existing_workflows) > 1:
                print("NATIVE_CI_MAPPING_REQUIRED=" + ",".join(existing_workflows))
                raise SystemExit("FAIL native CI mode requires explicit --native-ci-workflow mapping")
            else:
                raise SystemExit("FAIL native CI mode selected but no project-native workflow exists")
        missing_native = [path for path in native_ci_workflows if not (root / path).is_file()]
        if missing_native:
            raise SystemExit("FAIL mapped native CI workflow missing: " + ",".join(missing_native))
    else:
        native_ci_workflows = []

    ensure_knowledge_contract_compatible(root)
    ensure_runtime_contract_compatible(root)
    ensure_skills_contract_compatible(root)
    ensure_verification_contract_compatible(root)
    ensure_user_acceptance_compatible(root)
    ensure_agent_runtime_compatible(root)
    ensure_implementation_preflight_compatible(root)
    ensure_terminal_completion_notify_compatible(root)
    ensure_execution_profile_compatible(root)
    ensure_context_epoch_compatible(root)
    ensure_engineering_context_compatible(root)
    ensure_governance_floor_compatible(root)
    planned_execution_policy = plan_execution_policy_sync(root)

    removed_retired_agent_artifacts = remove_retired_agent_artifacts(root)

    written: list[str] = []
    skipped: list[str] = []

    write_missing(root, "AGENTS.md", (CANONICAL / "templates" / "AGENTS.md").read_text(encoding="utf-8"), written, skipped)
    execution_policy_synced = apply_execution_policy_sync(root, planned_execution_policy)
    for rel in (
        *KNOWLEDGE_CONTRACT_MANAGED,
        *RUNTIME_CONTRACT_MANAGED,
        *SKILLS_CONTRACT_MANAGED,
        *VERIFICATION_CONTRACT_MANAGED,
        *USER_ACCEPTANCE_MANAGED,
        *AGENT_RUNTIME_MANAGED,
        *IMPLEMENTATION_PREFLIGHT_MANAGED,
        *TERMINAL_COMPLETION_NOTIFY_MANAGED,
        *EXECUTION_PROFILE_MANAGED,
        *CONTEXT_EPOCH_MANAGED,
        *ENGINEERING_CONTEXT_MANAGED,
        *GOVERNANCE_FLOOR_MANAGED,
        *AFFECTED_TEST_SELECTION_MANAGED,
        *ENGINEERING_SYSTEM_DEPENDENCIES_MANAGED,
    ):
        write_missing(
            root,
            rel,
            (CANONICAL / rel).read_text(encoding="utf-8"),
            written,
            skipped,
        )
    write_missing(
        root,
        ".github/ISSUE_TEMPLATE/ai-work-packet.md",
        (CANONICAL / "templates" / ".github" / "ISSUE_TEMPLATE" / "ai-work-packet.md").read_text(encoding="utf-8"),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/project.yaml",
        project_yaml(
            root,
            version,
            baseline,
            ci_mode,
            native_ci_workflows,
            merge_gate_status,
            project_type,
            args.maturity,
            args.user_facing,
            args.primary_user_surface,
            domains,
            args.platform,
            operations_mode,
            args.persistent_state,
            runbook_paths,
            health_command,
            backup_command,
            restore_test_command,
            upgrade_command,
            rollback_command,
        ),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/tests.yaml",
        tests_yaml(domain_map, setup_command, test_command, domain_tests, quality_commands, args.platform),
        written,
        skipped,
    )
    write_missing(
        root,
        ".engineering/release.yaml",
        release_yaml(
            args.release_setup_command.strip(),
            args.preflight_command.strip(),
            args.release_command.strip(),
            args.artifact_hash_command.strip(),
            args.provenance_command.strip(),
            args.sbom_command.strip(),
            operations_mode,
            args.release_execution_context,
            operational_e2e_command,
            args.full_e2e_passes,
            public_smoke_command,
            args.user_facing,
            args.primary_user_surface,
            args.surface_reconciliation_contract.strip(),
            args.full_user_e2e_contract.strip(),
        ),
        written,
        skipped,
    )
    write_missing(
        root,
        ".github/workflows/engineering-system.yml",
        engineering_workflow(baseline, ci_mode),
        written,
        skipped,
    )
    release_contract_enabled = any(
        [
            args.release_command.strip(),
            args.release_setup_command.strip(),
            args.preflight_command.strip(),
            args.artifact_hash_command.strip(),
            args.provenance_command.strip(),
            args.sbom_command.strip(),
            operational_e2e_command,
            public_smoke_command,
        ]
    )
    if release_contract_enabled:
        write_missing(
            root,
            ".github/workflows/engineering-release.yml",
            release_workflow(baseline),
            written,
            skipped,
        )

    checker = CANONICAL / "tools" / "check-adoption.py"
    result = subprocess.run([sys.executable, str(checker), "--root", str(root)])
    if result.returncode:
        raise SystemExit(result.returncode)

    print(f"ENGINEERING_SYSTEM_VERSION={version}")
    print(f"ENGINEERING_SYSTEM_BASELINE={baseline}")
    print(f"ENGINEERING_SYSTEM_CI_MODE={ci_mode}")
    print("NATIVE_CI_WORKFLOWS=" + (",".join(native_ci_workflows) if native_ci_workflows else "<none>"))
    print(f"MERGE_GATE_ENFORCEMENT={merge_gate_status}")
    print(f"MERGE_GATE_OBSERVED={detected_merge_gate}")
    print(f"OPERATIONS_MODE={operations_mode}")
    print("DOMAINS=" + ",".join(domains))
    print("FILES_WRITTEN=" + (",".join(written) if written else "<none>"))
    print("FILES_PRESERVED=" + (",".join(skipped) if skipped else "<none>"))
    print("EXECUTION_POLICY_SYNCED=" + ("YES" if execution_policy_synced else "NO"))
    print("RETIRED_AGENT_ARTIFACTS_REMOVED=" + (",".join(removed_retired_agent_artifacts) if removed_retired_agent_artifacts else "<none>"))
    if setup_command:
        print(f"SETUP_COMMAND={setup_command}")
    else:
        print("SETUP_COMMAND=<none>")
    if test_command:
        print(f"TEST_COMMAND={test_command}")
    elif domain_tests:
        print("DOMAIN_TESTS=" + json.dumps(domain_tests, sort_keys=True))
    else:
        print("TEST_COMMAND=<explicitly-none>")
    print("QUALITY_COMMANDS=" + (json.dumps(quality_commands, sort_keys=True) if quality_commands else "<none>"))
    if release_contract_enabled:
        print("RELEASE_AUTOMATION=WIRED")
    else:
        print("RELEASE_AUTOMATION=NOT_APPLICABLE_OR_PENDING_EXPLICIT_COMMAND")
    print("ADOPTION_BOOTSTRAP=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
