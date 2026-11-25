import hashlib
import tempfile
import unittest
from pathlib import Path

from specification_integrity import inspect, unique_object


class SpecificationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put("spec.md", "Current requirement: default port is 6801.")
        self.put("code.py", "DEFAULT_PORT = 6801")
        self.put("test.py", "assert DEFAULT_PORT == 6801")
        self.manifest = {
            "version": 1,
            "inventory_globs": ["*.md"],
            "documents": [self.document("spec.md", "current", scope="config")],
            "requirements": [{
                "id": "REQ-1",
                "source": {"path": "spec.md", "anchor": "default port is 6801"},
                "acceptance": "No explicit port yields 6801",
                "state": "linked",
                "implementation": [{"path": "code.py", "anchor": "DEFAULT_PORT = 6801"}],
                "tests": [{"path": "test.py", "anchor": "assert DEFAULT_PORT == 6801"}],
            }],
        }

    def put(self, path, content):
        (self.root / path).write_text(content)

    def document(self, path, state, **extra):
        return {"path": path, "state": state, "sha256": hashlib.sha256((self.root/path).read_bytes()).hexdigest(), **extra}

    def check(self, complete=True):
        return inspect(self.root, self.manifest, complete)

    def test_structural_pass_not_runtime_pass(self):
        result = self.check()
        self.assertEqual(result["status"], "STRUCTURAL_PASS")
        self.assertEqual(result["test_execution"], "NOT_ESTABLISHED")
        self.assertEqual(result["semantic_coverage"], "NOT_ESTABLISHED")

    def test_changed_spec(self):
        self.put("spec.md", "Current requirement: default port is 7801.")
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_changed_doc_outside_anchor(self):
        self.put("spec.md", "Current requirement: default port is 6801. Conflicting addition.")
        self.assertIn("DOCUMENT_CHANGED_REVIEW_REQUIRED:spec.md", self.check()["errors"])

    def test_changed_code(self):
        self.put("code.py", "DEFAULT_PORT = 7801")
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_missing_test(self):
        (self.root/"test.py").unlink()
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_duplicate_authority(self):
        self.put("old.md", "Old specification")
        self.manifest["documents"].append(self.document("old.md", "current", scope="config"))
        self.assertIn("DUPLICATE_AUTHORITY:config", self.check()["errors"])

    def test_unclassified_spec(self):
        self.put("new.md", "Another specification")
        self.assertIn("UNCLASSIFIED_DOCUMENT:new.md", self.check()["errors"])

    def test_historical_requirement(self):
        self.manifest["documents"][0].update(state="historical", reason="retired")
        self.assertIn("NONCURRENT_REQUIREMENT_SOURCE:REQ-1", self.check()["errors"])

    def test_mixed_document(self):
        self.put("mixed.md", "Old and new")
        self.manifest["documents"].append(self.document("mixed.md", "mixed"))
        self.assertEqual(self.check(False)["status"], "INCOMPLETE")
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_missing_links(self):
        self.manifest["requirements"][0]["tests"] = []
        self.assertIn("MISSING_TESTS:REQ-1", self.check()["gaps"])
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_duplicate_id(self):
        self.manifest["requirements"].append(self.manifest["requirements"][0])
        self.assertIn("DUPLICATE_REQUIREMENT:REQ-1", self.check()["errors"])

    def test_duplicate_json_keys(self):
        with self.assertRaises(ValueError):
            unique_object([("id", 1), ("id", 2)])

    def test_ambiguous_anchor(self):
        self.put("test.py", "assert DEFAULT_PORT == 6801\nassert DEFAULT_PORT == 6801")
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_symlink_reference(self):
        (self.root/"test.py").unlink()
        (self.root/"test.py").symlink_to(self.root/"code.py")
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_traversal(self):
        self.manifest["requirements"][0]["tests"][0]["path"] = "../test.py"
        self.assertEqual(self.check()["status"], "BLOCK")

    def test_unresolved_semantic_finding(self):
        self.manifest["open_findings"] = ["Conflicting behavior in manual"]
        self.assertEqual(self.check()["status"], "BLOCK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
