import json,sys,tempfile,subprocess,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from web_guidance_contract import summarize,read_json,exact_checkout,scoped_project,EvidenceBlocked
SHA="a"*40
def legacy():
 return {"totalFindings":2,"guides":[{"guideId":"js","rules":[{"ruleId":"js/copying-array-methods","count":2,"findings":[{"file":"src/a.ts","line":2,"lineContent":"SECRET_NEVER_COPY"},{"file":"src/b.ts","line":3}]}]}]}
def browser():
 return {"receipt":"ES_MODERN_WEB_REAL_CONSUMER_A11Y_V1","source_head":SHA,"external_navigation":"BLOCKED","full_user_e2e_gate":"NOT_ESTABLISHED","passes":20,"violations":[{"id":"color-contrast","impact":"serious","nodes":15,"target":"SECRET_TARGET"}]}
class Tests(unittest.TestCase):
 def summary(self,l=None,b=None):
  return summarize(legacy() if l is None else l,b,source_sha=SHA,project="parser-studio",legacy_digest="b"*64,browser_digest="c"*64)
 def test_result_redacted(self):
  d=self.summary(b=browser());j=json.dumps(d)
  assert d["modernization_suggestion_count"]==2 and d["automated_wcag_violating_nodes"]==15
  assert d["mutation_authority"]=="NO" and d["full_wcag_conformance"]=="NOT_ESTABLISHED"
  assert d["scanner_input_attestation"]=="CALLER_SUPPLIED_NOT_INDEPENDENTLY_ATTESTED"
  assert "SECRET" not in j and "src/a.ts" not in j
 def test_no_wcag_auto_pass(self):
  d=self.summary();assert d["automated_wcag_status"]=="NOT_TESTED"
 def test_fail_closed(self):
  for key,value in [("source_head","b"*40),("external_navigation","UNVERIFIED"),("full_user_e2e_gate","PASS")]:
   b=browser();b[key]=value
   with self.assertRaises(EvidenceBlocked):self.summary(b=b)
  l=legacy();l["totalFindings"]=3
  with self.assertRaises(EvidenceBlocked):self.summary(l=l)
  l=legacy();l["guides"][0]["rules"][0]["findings"][0]["file"]="../secret"
  with self.assertRaises(EvidenceBlocked):self.summary(l=l)
 def test_pinned_toolchain(self):
  root=Path(__file__).resolve().parents[1]
  package=json.loads((root/"adapters/web-guidance/package.json").read_text())
  lock=json.loads((root/"adapters/web-guidance/package-lock.json").read_text())
  assert package["dependencies"]["modern-web-guidance"]=="1.0.1"
  assert package["dependencies"]["@axe-core/playwright"]=="4.14.0"
  assert lock["packages"]["node_modules/modern-web-guidance"]["integrity"].startswith("sha512-")
  assert lock["packages"]["node_modules/@axe-core/playwright"]["integrity"].startswith("sha512-")
 def test_read_and_git(self):
  with tempfile.TemporaryDirectory() as t:
   d=Path(t);p=d/"report.json";p.write_text(json.dumps(legacy()))
   assert read_json(p)[0]["totalFindings"]==2
   (d/"linked.json").symlink_to(p)
   with self.assertRaises(EvidenceBlocked):read_json(d/"linked.json")
   (d/".engineering").mkdir()
   (d/".gitignore").write_text("*.json\n")
   policy=d/".engineering/project.yaml"
   policy.write_text("project:\n  name: demo-web\n  user_facing: true\n  primary_user_surface: web\n")
   for c in [("init","-q"),("config","user.email","qa@example.invalid"),("config","user.name","QA"),("add",".engineering/project.yaml",".gitignore"),("commit","-qm","init")]:
    subprocess.run(["git","-C",str(d),*c],check=True,capture_output=True)
   sha=subprocess.check_output(["git","-C",str(d),"rev-parse","HEAD"],text=True).strip()
   exact_checkout(d,sha);assert scoped_project(d)=="demo-web"
   (d/"untracked.txt").write_text("dirty")
   with self.assertRaisesRegex(EvidenceBlocked,"WORKTREE_DIRTY"):exact_checkout(d,sha)
   (d/"untracked.txt").unlink()
   with self.assertRaises(EvidenceBlocked):exact_checkout(d,SHA)
   policy.write_text("project:\n  name: backend\n  user_facing: false\n  primary_user_surface: api\n")
   with self.assertRaises(EvidenceBlocked):scoped_project(d)
if __name__=="__main__":unittest.main()
