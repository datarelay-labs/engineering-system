#!/usr/bin/env python3
"""Integrity checks for the *informative* public-source standards crosswalk.

Never treats policy mappings, CI or self-authored records as certification/evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "standards" / "external-reference-map.yaml"
PUBLIC_HOSTS = frozenset({
    "www.iso.org", "csrc.nist.gov", "airc.nist.gov",
    "genai.owasp.org", "slsa.dev", "dora.dev", "modelcontextprotocol.io",
})
KINDS = frozenset({
    "international_standard", "government_guidance", "industry_guidance",
    "industry_specification", "industry_research",
})
PUBLICATIONS = frozenset({
    "published", "approved", "published_revision_in_development",
    "final_revision_draft",
})
APPLICABILITY = frozenset({
    "general", "conditional", "agent_execution", "release_artifacts",
    "conditional_ai_management", "conditional_ai_product", "measurement",
})
MAPPINGS = frozenset({"partial", "reference_only", "existing_policy"})
PRIORITIES = frozenset({"P0", "P1", "P2"})


class InvalidCrosswalk(ValueError):
    pass


def _repo_file(root: Path, path: object, *, policy: bool) -> None:
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", path):
        raise InvalidCrosswalk("unsafe or invalid repository path")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise InvalidCrosswalk("noncanonical repository path: " + path)
    if policy and not path.startswith("standards/"):
        raise InvalidCrosswalk("policy_paths must point to canonical standards/")
    target = root / path
    repo_root = root.resolve()
    allowed_root = (root / "standards").resolve() if policy else repo_root
    resolved = target.resolve()
    if (not target.is_file() or not resolved.is_relative_to(repo_root)
            or not resolved.is_relative_to(allowed_root)):
        raise InvalidCrosswalk("missing or escaping repository path: " + path)



def _direct_test_script(command: object) -> str | None:
    # Intentionally conservative: textual mentions, wrappers, OR/AND shell
    # expressions and opaque commands are not proof of direct test selection.
    if not isinstance(command, str):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or argv[0] not in {"python", "python3"}:
        return None
    if len(argv) == 3 and argv[1] == "-B":
        argv = [argv[0], argv[2]]
    if len(argv) != 2:
        return None
    script = argv[1]
    if not re.fullmatch(r"tools/test_[A-Za-z0-9_]+\.py", script):
        return None
    return script


def _manifest_bindings(root: Path) -> dict[str, list[str]]:
    path = root / ".engineering/tests.yaml"
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidCrosswalk("missing or invalid canonical test manifest") from exc
    scenarios = manifest.get("scenarios") if isinstance(manifest, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        raise InvalidCrosswalk("test manifest lacks scenarios")
    mapped: dict[str, list[str]] = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise InvalidCrosswalk("test scenario is not an object")
        sid = scenario.get("id")
        script = _direct_test_script(scenario.get("command"))
        if script:
            if not isinstance(sid, str) or not sid:
                raise InvalidCrosswalk("direct test scenario lacks ID")
            mapped.setdefault(script, []).append(sid)
    return mapped


def _trace_report(doc: dict, root: Path) -> dict:
    # This is a manifest linkage report, NOT executed CI, standard
    # conformance, risk coverage or independently witnessed Chat evidence.
    bindings = _manifest_bindings(root)
    records = []
    for item in doc["references"]:
        scripts = item["evidence_paths"]
        records.append({
            "reference_id": item["id"],
            "mapping_state": item["mapping_state"],
            "candidate_test_paths": scripts,
            "scenario_ids": sorted({id_ for p in scripts for id_ in bindings[p]}),
            "runtime_evidence": "NOT_ESTABLISHED",
        })
    return {
        "kind": "external-standards-manifest-trace",
        "basis": "CONFIGURATION_ONLY_NOT_TEST_EXECUTION",
        "total_references": len(records),
        "scenario_linked_references": sum(bool(r["scenario_ids"]) for r in records),
        "unlinked_references": sum(not r["scenario_ids"] for r in records),
        "exact_head_test_evidence": "NOT_ESTABLISHED",
        "live_agent_evidence": "NOT_VERIFIED",
        "conformance_evidence": "NOT_ESTABLISHED",
        "references": records,
    }

def validate(doc: object, root: Path) -> int:
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise InvalidCrosswalk("unsupported catalog schema_version")
    if not re.fullmatch(r"20\d\d-\d\d-\d\d", str(doc.get("reviewed_on", ""))):
        raise InvalidCrosswalk("review date must be YYYY-MM-DD")
    entries = doc.get("references")
    if not isinstance(entries, list) or len(entries) < 10:
        raise InvalidCrosswalk("missing or implausibly small reference set")
    scenario_bindings = _manifest_bindings(root)
    ids = set()
    required = {
        "id", "title", "type", "source_url", "publication_state",
        "applicability", "priority", "mapping_state", "policy_paths",
        "evidence_paths", "gap", "next_action", "conformance_claim",
    }
    for item in entries:
        if not isinstance(item, dict) or set(item) != required:
            raise InvalidCrosswalk("incomplete or unexpected crosswalk fields")
        ident = item["id"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]+", ident) or ident in ids:
            raise InvalidCrosswalk("invalid/duplicate reference ID")
        ids.add(ident)
        source = item["source_url"]
        try:
            url = urlsplit(source)
        except (TypeError, ValueError) as exc:
            raise InvalidCrosswalk("malformed official source URL") from exc
        if (url.scheme != "https" or url.hostname not in PUBLIC_HOSTS
                or url.username or url.password or not url.path.startswith("/")
                or url.query or url.fragment):
            raise InvalidCrosswalk("non-canonical or non-public source URL")
        for name, values in (
            ("type", KINDS),
            ("publication_state", PUBLICATIONS),
            ("applicability", APPLICABILITY),
            ("priority", PRIORITIES),
            ("mapping_state", MAPPINGS),
        ):
            if item[name] not in values:
                raise InvalidCrosswalk("invalid " + name)
        if item["conformance_claim"] is not False:
            raise InvalidCrosswalk("crosswalk must not self-assert compliance")
        for field in ("title", "gap", "next_action"):
            if not isinstance(item[field], str) or len(item[field].strip()) < 8:
                raise InvalidCrosswalk("missing/too-short " + field)
        policies, evidence = item["policy_paths"], item["evidence_paths"]
        if not isinstance(policies, list) or not policies or len(policies) != len(set(policies)):
            raise InvalidCrosswalk("missing/duplicate canonical mappings")
        if not isinstance(evidence, list) or len(evidence) != len(set(evidence)):
            raise InvalidCrosswalk("duplicate evidence paths")
        if item["mapping_state"] == "reference_only" and evidence:
            raise InvalidCrosswalk("reference-only source cannot claim test evidence")
        for path in policies:
            _repo_file(root, path, policy=True)
        for path in evidence:
            _repo_file(root, path, policy=False)
            if path not in scenario_bindings:
                raise InvalidCrosswalk(
                    "candidate test is not a directly selected manifest scenario: " + path
                )
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Informative standards manifest linkage")
    parser.add_argument("--report", action="store_true", help="JSON trace, not an execution receipt")
    args = parser.parse_args()
    doc = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    count = validate(doc, ROOT)
    if args.report:
        print(json.dumps(_trace_report(doc, ROOT), indent=2, sort_keys=True))
        return
    item = copy.deepcopy(doc)
    item["references"][1]["id"] = item["references"][0]["id"]
    _reject(item, "duplicate IDs")
    item = copy.deepcopy(doc)
    item["references"][0]["policy_paths"] = ["standards/MISSING.md"]
    _reject(item, "nonexistent policy")
    item = copy.deepcopy(doc)
    item["references"][0]["policy_paths"] = ["standards/../AGENTS.md"]
    _reject(item, "policy path escapes standards directory")
    item = copy.deepcopy(doc)
    item["references"][0]["policy_paths"] = ["standards/./CORE.md"]
    _reject(item, "policy path contains noncanonical dot segments")
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "standards").mkdir()
        (fake / "AGENTS.md").write_text("outside policy scope", encoding="utf-8")
        (fake / "standards" / "alias.md").symlink_to("../AGENTS.md")
        try:
            _repo_file(fake, "standards/alias.md", policy=True)
        except InvalidCrosswalk:
            pass
        else:
            raise AssertionError("policy symlink escaped standards directory")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
        fake = Path(td)
        external = Path(outside)
        (external / "CORE.md").write_text("outside repository", encoding="utf-8")
        (fake / "standards").symlink_to(external, target_is_directory=True)
        try:
            _repo_file(fake, "standards/CORE.md", policy=True)
        except InvalidCrosswalk:
            pass
        else:
            raise AssertionError("standards directory symlink escaped repository")
    item = copy.deepcopy(doc)
    item["references"][0]["source_url"] = "https://untrusted.example/standard"
    _reject(item, "unknown authority")
    item = copy.deepcopy(doc)
    item["references"][0]["conformance_claim"] = True
    _reject(item, "false certification")
    item = copy.deepcopy(doc)
    item["references"][0]["priority"] = "P-UNKNOWN"
    _reject(item, "invalid priority")
    item = copy.deepcopy(doc)
    item["references"][0]["evidence_paths"] = ["../secrets.txt"]
    _reject(item, "path traversal")
    item = copy.deepcopy(doc)
    item["references"][0]["evidence_paths"] = ["tools/validate.py"]
    _reject(item, "existing non-test tool is not a direct scenario")
    item = copy.deepcopy(doc)
    item["references"][0]["evidence_paths"] = ["tools/test_lifecycle_audit.py"]
    _reject(item, "existing test file but not a direct scenario")
    assert _direct_test_script("python3 tools/test_behavior_eval.py") == "tools/test_behavior_eval.py"
    assert _direct_test_script("python3 -B tools/test_behavior_eval.py") == "tools/test_behavior_eval.py"
    for false_command in (
        "echo python3 tools/test_behavior_eval.py",
        "python3 tools/test_behavior_eval.py || true",
        "python3 -c 'print(1)'",
        "bash -lc 'python3 tools/test_behavior_eval.py'",
    ):
        assert _direct_test_script(false_command) is None, false_command
    trace = _trace_report(doc, ROOT)
    assert trace["total_references"] == count
    assert trace["exact_head_test_evidence"] == "NOT_ESTABLISHED"
    assert trace["live_agent_evidence"] == "NOT_VERIFIED"
    print(f"PASS external references: {count} source mappings; no conformance claims")


def _reject(document: dict, case: str) -> None:
    try:
        validate(document, ROOT)
    except InvalidCrosswalk:
        return
    raise AssertionError("invalid crosswalk accepted: " + case)


if __name__ == "__main__":
    main()
