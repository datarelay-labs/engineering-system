#!/usr/bin/env python3
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

import specification_contract as spec


class SpecificationContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put("spec.md", "Only two parser creation modes.\nDefault listener port is 6801.\n")
        self.put("old.md", "Legacy Workbench navigation.\n")
        self.put("app.py", "PARSER_MODES = ('auto', 'assisted')\n")
        self.put("test_app.py", "def test_two_modes(): pass\n")
        self.manifest = {
            "version": 1,
            "documents": {
                p: {"status": status, "sha256": spec.digest((self.root / p).read_bytes())}
                for p, status in [("spec.md", "active"), ("old.md", "historical")]
            },
            "requirements": [{
                "id": "PS-001", "state": "implemented",
                "source": {"path": "spec.md", "contains": "Only two parser creation modes."},
                "implementation": [{"path": "app.py", "contains": "PARSER_MODES = ('auto', 'assisted')"}],
                "tests": [{"path": "test_app.py", "contains": "def test_two_modes()"}],
            }],
        }

    def put(self, path, data):
        (self.root / path).write_text(data, encoding="utf-8")

    def check(self):
        return spec.validate(self.root, self.manifest)

    def fail_with(self, msg):
        with self.assertRaisesRegex(spec.Invalid, msg):
            self.check()

    def test_structural_pass_not_semantic_proof(self):
        self.assertEqual(self.check()["implemented"], 1)
        self.put("manifest.json", json.dumps(self.manifest))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(spec.main(["--root", str(self.root), "--manifest", "manifest.json"]), 0)
        self.assertIn("SEMANTIC_CONSISTENCY=UNVERIFIED", out.getvalue())
        self.assertIn("AUTHORIZES_RELEASE=NO", out.getvalue())

    def test_spec_change(self):
        self.put("spec.md", "Only three parser creation modes.")
        self.fail_with("specification changed")

    def test_mixed_document(self):
        self.manifest["documents"]["old.md"]["status"] = "mixed"
        self.fail_with("unresolved active/historical")

    def test_historical_authority(self):
        self.manifest["requirements"][0]["source"] = {"path": "old.md", "contains": "Legacy Workbench navigation."}
        self.fail_with("historical source")

    def test_missing_test(self):
        self.manifest["requirements"][0]["tests"] = []
        self.fail_with("missing tests trace")

    def test_deleted_test_assertion_marker(self):
        self.put("test_app.py", "def unrelated_test(): pass")
        self.fail_with("marker missing")

    def test_missing_implementation(self):
        self.manifest["requirements"][0]["implementation"] = []
        self.fail_with("missing implementation trace")

    def test_duplicate_id(self):
        self.manifest["requirements"].append(self.manifest["requirements"][0].copy())
        self.fail_with("duplicate requirement ID")

    def test_duplicate_authority(self):
        self.manifest["requirements"].append(dict(self.manifest["requirements"][0], id="PS-002"))
        self.fail_with("duplicate authority marker")

    def test_doc_cannot_replace_test(self):
        self.manifest["requirements"][0]["tests"] = [{"path": "spec.md", "contains": "Only two parser creation modes."}]
        self.fail_with("cannot substitute")

    def test_source_cannot_replace_test(self):
        self.manifest["requirements"][0]["tests"] = self.manifest["requirements"][0]["implementation"]
        self.fail_with("implementation cannot substitute")

    def test_untracked_authority(self):
        self.put("extra.md", "Another product requirement.")
        self.manifest["requirements"][0]["source"] = {"path": "extra.md", "contains": "Another product requirement."}
        self.fail_with("undeclared specification")

    def test_active_doc_without_requirement(self):
        self.manifest["documents"]["old.md"]["status"] = "active"
        self.fail_with("missing requirement inventory")

    def test_planned_not_release_ready(self):
        self.manifest["requirements"][0].update(state="planned", implementation=[], tests=[])
        self.assertEqual(self.check()["planned"], 1)
        with self.assertRaisesRegex(spec.Invalid, "planned requirements remain"):
            spec.validate(self.root, self.manifest, require_implemented=True)

    def test_retired_with_live_traces(self):
        self.manifest["requirements"][0]["state"] = "retired"
        self.fail_with("retired requirement has live traces")

    def test_path_escape(self):
        self.manifest["requirements"][0]["tests"][0]["path"] = "../test.py"
        self.fail_with("invalid repository path")

    def test_symlink(self):
        (self.root / "test_app.py").unlink()
        (self.root / "test_app.py").symlink_to(self.root / "app.py")
        self.fail_with("symlink is not permitted")

    def use_v2(self):
        self.put("spec.md", "<!-- requirement: PS-001 -->\nOnly two parser creation modes.\n")
        self.manifest["version"] = 2
        self.manifest["inventory_globs"] = ["*.md"]
        self.refresh("spec.md")

    def refresh(self, path):
        self.manifest["documents"][path]["sha256"] = spec.digest((self.root/path).read_bytes())

    def test_v2_scoped_pass(self):
        self.use_v2()
        self.assertEqual(self.check()["implemented"], 1)

    def test_v2_unclassified_file(self):
        self.use_v2()
        self.put("new.md", "Unclassified product scope.")
        self.fail_with("unclassified specification files")

    def test_v2_unmatched_glob(self):
        self.use_v2()
        self.manifest["inventory_globs"].append("missing/*.md")
        self.fail_with("matched no files")

    def test_v2_document_outside_scope(self):
        self.use_v2()
        self.manifest["inventory_globs"] = ["spec.md"]
        self.fail_with("declared specification outside scope")

    def test_v2_omitted_requirement(self):
        self.use_v2()
        with (self.root/"spec.md").open("a") as f:
            f.write("<!-- requirement: PS-002 -->\nDefault listener port is 6801.\n")
        self.refresh("spec.md")
        self.fail_with("declared requirement missing trace: PS-002")

    def test_v2_duplicate_tag(self):
        self.use_v2()
        with (self.root/"spec.md").open("a") as f:
            f.write("<!-- requirement: PS-001 -->\nConflicting requirement.\n")
        self.refresh("spec.md")
        self.fail_with("duplicate declared requirement tag")

    def test_v2_missing_tag(self):
        self.use_v2()
        self.put("spec.md", "Only two parser creation modes.\n")
        self.refresh("spec.md")
        self.fail_with("source marker is not uniquely bound")

    def test_v2_wrong_marker_section(self):
        self.use_v2()
        self.put("spec.md", "<!-- requirement: PS-001 -->\nDifferent statement.\n<!-- requirement: PS-002 -->\nOnly two parser creation modes.\n")
        self.refresh("spec.md")
        self.fail_with("source marker is not uniquely bound")

    def test_v2_ambiguous_source_marker(self):
        self.use_v2()
        self.put("spec.md", "Only two parser creation modes.\n<!-- requirement: PS-001 -->\nOnly two parser creation modes.\n")
        self.refresh("spec.md")
        self.fail_with("ambiguous source marker")

    def test_v2_malformed_marker(self):
        self.use_v2()
        with (self.root/"spec.md").open("a") as f:
            f.write("<!-- requirement: PS-INVALID -->\n")
        self.refresh("spec.md")
        self.fail_with("malformed requirement tag")

    def test_v2_supporting_doc_cannot_authorize(self):
        self.use_v2()
        self.manifest["documents"]["old.md"]["status"] = "supporting"
        self.assertEqual(self.check()["implemented"], 1)
        self.manifest["requirements"][0]["source"] = {"path": "old.md", "contains": "Legacy Workbench navigation."}
        self.fail_with("historical source")

    def test_baseline_prevents_silent_removal_of_previously_live_requirement(self):
        self.use_v2()
        text = "Default listener port is 6801."
        self.put("spec.md", "<!-- requirement: PS-001 -->\nOnly two parser creation modes.\n"
                 "<!-- requirement: PS-002 -->\n" + text + "\n")
        self.refresh("spec.md")
        self.manifest["requirements"].append({
            "id": "PS-002", "state": "planned", "source": {"path": "spec.md", "contains": text},
            "implementation": [], "tests": [],
        })
        self.check()
        self.put("manifest.json", json.dumps(self.manifest))
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.root), *args])
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "approved")
        base = git("rev-parse", "HEAD").decode().strip()
        # Joint removal of the marker and manifest entry used to pass.
        self.manifest["requirements"].pop()
        self.put("spec.md", "<!-- requirement: PS-001 -->\nOnly two parser creation modes.\n")
        self.refresh("spec.md")
        self.put("manifest.json", json.dumps(self.manifest))
        self.assertEqual(self.check()["implemented"], 1)
        args = ["--root", str(self.root), "--manifest", "manifest.json", "--base", base]
        with contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(spec.main(args), 1)
        self.assertIn("previously live requirement missing: PS-002", out.getvalue())
        # Explicit retirement retains the ID without pretending implementation.
        self.put("old.md", text)
        self.refresh("old.md")
        self.manifest["requirements"].append({
            "id": "PS-002", "state": "retired", "source": {"path": "old.md", "contains": text},
            "implementation": [], "tests": [],
        })
        self.put("manifest.json", json.dumps(self.manifest))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(spec.main(args), 0)
        self.assertIn("PS-002", out.getvalue())
        self.assertIn("BASELINE_REVIEW=REQUIRED", out.getvalue())

    def test_requirement_lookup_reuses_authority_and_code_test_paths(self):
        self.use_v2()
        self.put("manifest.json", json.dumps(self.manifest))
        args = ["--root", str(self.root), "--manifest", "manifest.json", "--requirement", "PS-001"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(spec.main(args), 0)
        result = out.getvalue()
        self.assertIn("REQUIREMENT_TRACE=", result)
        self.assertIn("spec.md", result)
        self.assertIn("app.py", result)
        self.assertIn("test_app.py", result)
        self.assertIn("TEST_EXECUTION=UNVERIFIED", result)
        with contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(spec.main(args[:-1] + ["PS-999"]), 1)
        self.assertIn("unknown requirement: PS-999", out.getvalue())

    def test_baseline_delta_identifies_scope_changes_and_downgraded_states(self):
        self.use_v2()
        base = copy.deepcopy(self.manifest)
        self.manifest["requirements"][0].update(state="planned", implementation=[], tests=[])
        self.manifest["inventory_globs"] = ["spec.md", "old.md"]
        self.check()
        delta = spec.baseline_delta(base, self.manifest)
        self.assertEqual(delta["state_changes"], {"PS-001": ["implemented", "planned"]})
        self.assertEqual(delta["removed_globs"], ["*.md"])
        self.assertEqual(delta["added_globs"], ["old.md", "spec.md"])

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(spec.Invalid, "duplicate JSON key"):
            json.loads('{"version":1,"version":2}', object_pairs_hook=spec.unique_object)


if __name__ == "__main__":
    unittest.main(verbosity=2)
