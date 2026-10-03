import json,subprocess,unittest
from pathlib import Path
TOOL=Path(__file__).with_name("atlas-context-contract.py")
def run(*args):
 return subprocess.run(["python3",str(TOOL),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
class Tests(unittest.TestCase):
 def test_request_is_derived_and_has_canonical_fallback(self):
  r=run("request","--project-id","datarelay-atlas","--repository","datarelay-labs/datarelay-atlas","--workstream","m5","--task","resume memory work")
  self.assertEqual(r.returncode,0);x=json.loads(r.stdout);self.assertEqual(x["authority"],"DERIVED_NON_AUTHORITATIVE");self.assertEqual(x["fallback"],"CANONICAL_LOCAL_CONTEXT")
 def test_candidate_is_noncanonical_and_bounded(self):
  r=run("candidate","--project-id","datarelay-atlas","--repository","datarelay-labs/datarelay-atlas","--candidate-class","LESSON_LEARNED","--content","Use Atlas after canonical context.")
  self.assertEqual(r.returncode,0);x=json.loads(r.stdout);self.assertFalse(x["canonical"])
 def test_secret_and_unsafe_scope_fail_closed(self):
  self.assertNotEqual(run("candidate","--project-id","../x","--repository","datarelay-labs/datarelay-atlas","--candidate-class","RUN_SUMMARY","--content","safe").returncode,0)
  self.assertNotEqual(run("candidate","--project-id","x","--repository","datarelay-labs/datarelay-atlas","--candidate-class","RUN_SUMMARY","--content","password=unsafe-secret-value").returncode,0)
 def test_observation_is_content_free_policy_neutral(self):
  r=run("observation","--project-id","x","--repository","datarelay-labs/x","--workstream","w","--important-expected","2","--important-recalled","1","--stale-injected","0","--irrelevant-injected","0","--duplicate-injected","0","--injected-context-bytes","100","--repeated-owner-explanations","0","--first-pass-success")
  self.assertEqual(r.returncode,0);x=json.loads(r.stdout);self.assertTrue(x["content_free"]);self.assertFalse(x["policy_mutated"]);self.assertNotIn("task",x)
if __name__=="__main__":unittest.main()
