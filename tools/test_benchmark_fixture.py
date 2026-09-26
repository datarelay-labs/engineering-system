#!/usr/bin/env python3
"""Regressions for historical benchmark fixture freeze validation."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "benchmark_fixture.py"


def _fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def load_tool():
    spec = importlib.util.spec_from_file_location("benchmark_fixture", TOOL)
    if spec is None or spec.loader is None:
        raise SystemExit("FAIL cannot load benchmark_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = load_tool()


def _manifest() -> dict:
    return yaml.safe_load(FIXTURE.MANIFEST_PATH.read_text(encoding="utf-8"))


def _expect(document: dict, code: str) -> None:
    try:
        FIXTURE.validate_manifest(document)
    except FIXTURE.FixtureError as exc:
        if exc.code != code:
            _fail(f"expected {code} but got {exc.code}")
        return
    _fail(f"expected {code}")


def test_canonical_manifest_freezes_proven_identities() -> None:
    document = FIXTURE.load_manifest()
    by_id = {case["id"]: case for case in document["cases"]}
    if document["control"]["head"] != FIXTURE.CONTROL_HEAD:
        _fail("control anchor drifted")
    if document["candidate"]["head"] != FIXTURE.CANDIDATE_HEAD:
        _fail("candidate anchor drifted")
    if document["candidate"]["tree"] != FIXTURE.CANDIDATE_TREE:
        _fail("candidate tree drifted")
    if "weighted_score" in document or "aggregate_score" in document:
        _fail("aggregate score was introduced")
    bug = by_id["BENCH-BUG-001"]
    if bug["source_commit"] != "3cdedad5a40105aea426abda0df8d7c258e5e8ad":
        _fail("bug fixture did not freeze the pre-fix snapshot")
    if bug["lineage_commits"] != ["a93327fbc88fd5ee5d579e290b7641cc68895f83"]:
        _fail("bug fixture lost the historical fix commit")
    docs = by_id["BENCH-DOCS-006"]
    if docs["source_commit"] != "066513fe7cc3b6416b5763f82d4c1c68a4e15ecc":
        _fail("docs fixture did not freeze the closing-history snapshot")
    if docs["lineage_commits"] != ["cd3b420dd511abea6d67ff6a6e908a15a30f99b6"]:
        _fail("docs fixture lost the referenced alignment commit")
    cli = by_id["BENCH-CLI-004"]
    if cli["identity_basis"] != "PRE_TASK_BASE_COMMIT":
        _fail("cli fixture does not start from the pre-task base")
    if cli["source_commit"] != "3f931c2708c867062224acf4eedd1aef48d3028f":
        _fail("cli fixture did not freeze the original pre-task start")
    if cli["source_commit"] in {
        "a333920275032684261b08b1f2cf3310fa339384",
        "17f8f122369685dc9ca2dc37ccb37b7882e387e7",
        "4b7c2ebc49f52acfa5e6184e7ba77096496f0a3e",
    }:
        _fail("cli fixture starts from a completed head")
    if cli["lineage_commits"] != [
        "17f8f122369685dc9ca2dc37ccb37b7882e387e7",
        "4b7c2ebc49f52acfa5e6184e7ba77096496f0a3e",
    ]:
        _fail("cli fixture lost the content or provenance lineage")
    incident = by_id["BENCH-INCIDENT-003"]
    if incident["identity_basis"] != "PRE_TASK_BASE_COMMIT":
        _fail("incident fixture does not start from the pre-task base")
    if incident["source_commit"] != "2412616607d405b22e311c963a0f40e8c0daba27":
        _fail("incident fixture did not freeze the pre-task start")
    if incident["source_commit"] == "322b061c2640eb03f2c4b5bb29bb62e7c81a6421":
        _fail("incident fixture starts from the completed merge")
    if incident["lineage_commits"] != [
        "e337089b0034206b2ee225eeacdf057117a5e552",
        "322b061c2640eb03f2c4b5bb29bb62e7c81a6421",
    ]:
        _fail("incident fixture lost the feature or merge lineage")
    ui = by_id["BENCH-UI-005"]
    if ui["identity_basis"] != "PRE_TASK_BASE_COMMIT":
        _fail("ui fixture does not start from the pre-task base")
    if ui["source_commit"] != "322b061c2640eb03f2c4b5bb29bb62e7c81a6421":
        _fail("ui fixture did not freeze the pre-task base")
    if ui["source_commit"] in {
        "bc6255c90959dfd03090b0f7da62cf2018b4693b",
        "3bc4ae48e1d5f7528045e5cbb368e0768194599f",
    }:
        _fail("ui fixture starts from a completed head")
    if ui["lineage_commits"] != [
        "bc6255c90959dfd03090b0f7da62cf2018b4693b",
        "3bc4ae48e1d5f7528045e5cbb368e0768194599f",
    ]:
        _fail("ui fixture lost the pull-request or merge lineage")
    for case in (cli, incident, ui):
        if case["source_commit"] in case["lineage_commits"]:
            _fail(f"{case['id']} starts from an already-fixed lineage head")
    multi = by_id["BENCH-MULTI-007"]
    if multi["identity_basis"] != "PRE_TASK_BASE_COMMIT":
        _fail("multi fixture does not start from the pre-task base")
    if multi["source_commit"] != "f3a6856a81c7bee307ca8a6cf256faa73514610e":
        _fail("multi fixture did not freeze the canonical rollout base")
    if multi["source_commit"] == FIXTURE.CONTROL_HEAD:
        _fail("multi fixture starts from the completed rollout merge")
    if multi["lineage_commits"] != [
        "3cb07ef3e95cbda49735154eb9d5c0d50b48bf09",
        FIXTURE.CONTROL_HEAD,
    ]:
        _fail("multi fixture lost the canonical rollout lineage")
    cross = by_id["BENCH-CROSS-002"]
    if cross["status"] != "FROZEN" or cross["identity_basis"] != "HISTORICAL_CHECKPOINT_COMMIT":
        _fail("cross fixture did not freeze the historical checkpoint")
    if cross["source_commit"] != "3088e0067dbada08267eeaae100ca2f42b3b0758":
        _fail("cross fixture commit drifted")
    if "block_reason" in cross:
        _fail("frozen cross fixture kept a block reason")
    required_oracle = {
        "PRE_FULL_E2E_QUALIFICATION_CORRECT",
        "STOP_AT_OWNER_GATED_FULL_REAL_E2E",
        "NO_FINAL_RELEASE_COMPLETION_CLAIM",
    }
    if not required_oracle.issubset(cross["oracle"]):
        _fail("cross oracle omitted the pre-Full-E2E stop rule")
    for case in document["cases"]:
        if not case["oracle"]:
            _fail(f"{case['id']} is missing an oracle")
        if case["excludes_secrets"] is not True or case["excludes_production_mutation"] is not True:
            _fail(f"{case['id']} exclusions are not machine-checkable")
        if case["status"] == "FROZEN" and case["reset"] != "FRESH_WORKTREE_SESSION":
            _fail(f"{case['id']} is missing reset metadata")
    encoded = json.dumps(document)
    for token in ("ghp_", "github_pat_", "BEGIN PRIVATE", "push --force"):
        if token in encoded:
            _fail(f"canonical manifest contained {token}")


def test_rejects_mutable_secret_and_mutation_metadata() -> None:
    mutable = _manifest()
    mutable["cases"][0]["title"] = "refs/heads/main"
    _expect(mutable, "MUTABLE_REF")
    named = _manifest()
    named["cases"][0]["title"] = "main"
    _expect(named, "MUTABLE_REF")
    secret = _manifest()
    secret["cases"][0]["title"] = "ghp_example"
    _expect(secret, "SECRET_BEARING_FIXTURE")
    mutation = _manifest()
    mutation["cases"][0]["title"] = "deploy to production"
    _expect(mutation, "PRODUCTION_MUTATION_INSTRUCTION")
    leaked = _manifest()
    leaked["cases"][0]["secret"] = "value"
    _expect(leaked, "PROHIBITED_FIXTURE_FIELD")
    scored = _manifest()
    scored["weighted_score"] = 1
    _expect(scored, "PROHIBITED_FIXTURE_FIELD")


def test_rejects_missing_oracle_and_unfrozen_identity() -> None:
    missing = _manifest()
    missing["cases"][0]["oracle"] = []
    _expect(missing, "SCHEMA_INVALID")
    short = _manifest()
    short["cases"][0]["source_commit"] = "3cdedad"
    _expect(short, "SCHEMA_INVALID")
    branch = _manifest()
    branch["cases"][0]["source_commit"] = "feature/v2.4.0-final-product-closure"
    _expect(branch, "SCHEMA_INVALID")
    blocked_with_commit = _manifest()
    blocked = blocked_with_commit["cases"][1]
    blocked["status"] = "BLOCKED"
    blocked["block_reason"] = "SOURCE_STILL_MOVING"
    for key in ("lineage_commits", "identity_basis", "reset"):
        blocked.pop(key, None)
    _expect(blocked_with_commit, "SCHEMA_INVALID")


def test_rejects_anchor_field_and_case_drift() -> None:
    anchor = _manifest()
    anchor["candidate"]["head"] = "b" * 40
    _expect(anchor, "CANDIDATE_ANCHOR_MISMATCH")
    fields = _manifest()
    fields["result_fields"] = list(FIXTURE.RESULT_FIELDS)
    fields["result_fields"][-1] = "WEIGHTED_SCORE"
    _expect(fields, "RESULT_FIELDS_MISMATCH")
    dropped = _manifest()
    dropped["cases"] = dropped["cases"][1:]
    dropped["cases"].append(copy.deepcopy(dropped["cases"][0]))
    dropped["cases"][-1]["id"] = "BENCH-EXTRA-999"
    _expect(dropped, "CASE_SET_MISMATCH")
    evidence = _manifest()
    evidence["cases"][0]["evidence"] = ["CORRECT_BEHAVIOR"]
    _expect(evidence, "MISSING_EVIDENCE")


def test_worker_task_hides_oracle_and_issue_text() -> None:
    manifest = FIXTURE.load_manifest()
    for case in manifest["cases"]:
        task = FIXTURE.worker_task(manifest, case["id"])
        if set(FIXTURE.WORKER_TASK_KEYS) - set(task):
            _fail(f"{case['id']} worker task is not the sanitized contract")
        extra = set(task) - set(FIXTURE.WORKER_TASK_KEYS)
        allowed_extra = set()
        if case["id"] == "BENCH-INCIDENT-003":
            allowed_extra.add("evidence_input")
        if case["id"] == "BENCH-MULTI-007":
            allowed_extra.add("rollout_topology")
        if extra != allowed_extra:
            _fail(f"{case['id']} worker task exposed unexpected fields {extra}")
        encoded = json.dumps(task)
        if _has_key(task, "source_record"):
            _fail(f"{case['id']} worker task exposed a source record")
        for hidden in ("oracle", "lineage_commits", "fixture_id", "prompt", "conversation"):
            if hidden in task:
                _fail(f"{case['id']} worker task included {hidden}")
        for code in case["oracle"]:
            if code in encoded:
                _fail(f"{case['id']} worker task leaked oracle code {code}")
        if case["source_record"] in encoded:
            _fail(f"{case['id']} worker task pointed at mutable issue text")
        if "rollout_topology" not in task:
            for lineage in case["lineage_commits"]:
                if lineage in encoded:
                    _fail(f"{case['id']} worker task exposed outcome lineage")
        if task["source_commit"] != case["source_commit"] or task["objective"] != case["task"]["objective"]:
            _fail(f"{case['id']} worker task did not preserve the frozen objective")
    leaked = _manifest()
    leaked["cases"][0]["task"]["objective"] = leaked["cases"][0]["oracle"][0]
    _expect(leaked, "TASK_LEAKS_ORACLE")
    overlap = _manifest()
    overlap["cases"][0]["task"]["objective_codes"] = [overlap["cases"][0]["oracle"][0]]
    _expect(overlap, "TASK_ORACLE_OVERLAP")
    missing = _manifest()
    del missing["cases"][0]["task"]
    _expect(missing, "SCHEMA_INVALID")
    incident_task = FIXTURE.worker_task(manifest, "BENCH-INCIDENT-003")
    evidence_codes = [item["code"] for item in incident_task["evidence_input"]]
    if tuple(evidence_codes) != FIXTURE.INCIDENT_EVIDENCE_CODES:
        _fail("incident worker task dropped preserved evidence")
    if "root cause" in json.dumps(incident_task).lower():
        _fail("incident worker task stated a root-cause conclusion")
    concluded = _manifest()
    concluded["cases"][2]["evidence_input"][0]["fact"] = "The root cause is sustained swap exhaustion."
    _expect(concluded, "EVIDENCE_LEAKS_CONCLUSION")
    multi_case = next(case for case in manifest["cases"] if case["id"] == "BENCH-MULTI-007")
    multi_task = FIXTURE.worker_task(manifest, "BENCH-MULTI-007")
    projected = {
        (item["repository"], item["role"], item["base_commit"], item["head_commit"], item["state"])
        for item in multi_task["rollout_topology"]
    }
    recorded = {
        (item["repository"], item["role"], item["base_commit"], item["head_commit"], item["state"])
        for item in multi_case["rollout_topology"]
    }
    if projected != recorded:
        _fail("multi worker task dropped frozen rollout topology")
    superseded = next(item for item in multi_task["rollout_topology"] if item["role"] == "SUPERSEDED")
    if superseded["state"] != "CLOSED_UNMERGED" or "merge_commit" in superseded:
        _fail("superseded rollout was treated as merged")
    if superseded["head_commit"] != "8bc3283db9afa674a1e6de2b92925b4d1c723394":
        _fail("superseded rollout head drifted")
    if "api.github.com" in json.dumps(multi_task):
        _fail("multi worker task depends on live issue discovery")


def _has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def test_source_record_rejects_repository_prefix_collision() -> None:
    collided = _manifest()
    incident = next(case for case in collided["cases"] if case["id"] == "BENCH-INCIDENT-003")
    incident["source_record"] = "datarelay-labs/datarelay-control-evil#138"
    _expect(collided, "SOURCE_RECORD_MISMATCH")
    topology = _manifest()
    multi = next(case for case in topology["cases"] if case["id"] == "BENCH-MULTI-007")
    control = next(item for item in multi["rollout_topology"] if item["repository"] == "datarelay-labs/datarelay-control")
    control["source_record"] = "datarelay-labs/datarelay-control-evil#135"
    _expect(topology, "SOURCE_RECORD_MISMATCH")


def test_changed_task_cannot_retain_fixture_binding() -> None:
    manifest = FIXTURE.load_manifest()
    frozen_sha = "a" * 40
    case_id = "BENCH-BUG-001"
    original_id = FIXTURE.fixture_id(frozen_sha, case_id)
    if original_id != f"{frozen_sha}:{case_id}":
        _fail("fixture id is not the manifest commit plus case id")
    bound = FIXTURE.bind_fixture_id(original_id, manifest, frozen_sha)
    if bound["case_id"] != case_id or bound["manifest_git_sha"] != frozen_sha:
        _fail("fixture id did not bind the frozen manifest commit")
    mutated = copy.deepcopy(manifest)
    bug = next(case for case in mutated["cases"] if case["id"] == case_id)
    source_commit = bug["source_commit"]
    bug["task"]["objective"] = "Reproduce the reported gate failure on the frozen snapshot and land a bounded fix."
    if bug["source_commit"] != source_commit:
        _fail("adversarial edit changed the source commit")
    try:
        FIXTURE.validate_manifest(mutated)
    except FIXTURE.FixtureError as exc:
        _fail(f"MUTATED_VALIDATE failed as {exc.code}")
    later_sha = "b" * 40
    if FIXTURE.fixture_id(later_sha, case_id) == original_id:
        _fail("ID_UNCHANGED")
    try:
        FIXTURE.bind_fixture_id(original_id, mutated, later_sha)
    except FIXTURE.FixtureError as exc:
        if exc.code != "FIXTURE_REVISION_MISMATCH":
            _fail(f"stale task binding returned {exc.code}")
    else:
        _fail("changed task retained a valid result binding")


def test_tool_has_no_network_or_execution_surface() -> None:
    text = TOOL.read_text(encoding="utf-8")
    for banned in ("subprocess", "urlopen", "urllib", "requests", "socket", "os.system"):
        if banned in text:
            _fail(f"fixture validator contains {banned}")
    for required in (
        "MUTABLE_REF",
        "SECRET_BEARING_FIXTURE",
        "PRODUCTION_MUTATION_INSTRUCTION",
        "MISSING_ORACLE",
        "FIXTURE_REVISION_MISMATCH",
        "worker_task",
    ):
        if required not in text:
            _fail(f"fixture validator missing {required}")
    for banned_binding in ("hashlib", "sha256", "blake2"):
        if banned_binding in text:
            _fail(f"fixture id uses {banned_binding}")


def main() -> None:
    test_canonical_manifest_freezes_proven_identities()
    test_rejects_mutable_secret_and_mutation_metadata()
    test_rejects_missing_oracle_and_unfrozen_identity()
    test_rejects_anchor_field_and_case_drift()
    test_worker_task_hides_oracle_and_issue_text()
    test_source_record_rejects_repository_prefix_collision()
    test_changed_task_cannot_retain_fixture_binding()
    test_tool_has_no_network_or_execution_surface()
    print("PASS benchmark fixture freeze")


if __name__ == "__main__":
    sys.exit(main())
