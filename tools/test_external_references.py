#!/usr/bin/env python3
"""Integrity checks for the *informative* public-source standards crosswalk.

Never treats policy mappings, CI or self-authored records as certification/evidence.
"""
from __future__ import annotations

import copy
import re
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
    if policy and not path.startswith("standards/"):
        raise InvalidCrosswalk("policy_paths must point to canonical standards/")
    target = root / path
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise InvalidCrosswalk("missing or escaping repository path: " + path)


def validate(doc: object, root: Path) -> int:
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise InvalidCrosswalk("unsupported catalog schema_version")
    if not re.fullmatch(r"20\d\d-\d\d-\d\d", str(doc.get("reviewed_on", ""))):
        raise InvalidCrosswalk("review date must be YYYY-MM-DD")
    entries = doc.get("references")
    if not isinstance(entries, list) or len(entries) < 10:
        raise InvalidCrosswalk("missing or implausibly small reference set")
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
    return len(entries)


def main() -> None:
    doc = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    count = validate(doc, ROOT)
    item = copy.deepcopy(doc)
    item["references"][1]["id"] = item["references"][0]["id"]
    _reject(item, "duplicate IDs")
    item = copy.deepcopy(doc)
    item["references"][0]["policy_paths"] = ["standards/MISSING.md"]
    _reject(item, "nonexistent policy")
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
    print(f"PASS external references: {count} source mappings; no conformance claims")


def _reject(document: dict, case: str) -> None:
    try:
        validate(document, ROOT)
    except InvalidCrosswalk:
        return
    raise AssertionError("invalid crosswalk accepted: " + case)


if __name__ == "__main__":
    main()
