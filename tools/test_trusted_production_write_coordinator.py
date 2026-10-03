import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("prod_coord",ROOT/"tools"/"trusted_production_write_coordinator.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class A: pass

class ProductionCoordinatorTests(unittest.TestCase):
 def args(self,base):
  a=A();a.repository="datarelay-labs/datarelay-atlas";a.issue_id="268";a.root=ROOT
  a.request_json=base/"request.json";a.request_json.write_text("{}")
  a.workstream="production-rollout-post-v010";a.branch="ops/prod-rollout-d7667da";a.subject_head="a"*40
  a.intent_revision=2;a.session_id="s1";a.dispatch_id="d1";a.ttl_seconds=120
  a.binding_out=base/"binding";a.dispatch_out=base/"dispatch";return a
 def test_exact_active_high_packet_invokes_fixed_signer_only(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d))
   issue={"state":"open","title":"[AI Work] prod","body":"x","user":{"login":"owner"}}
   fields={"TARGET_REPO":a.repository,"WORKSTREAM":a.workstream,"STATUS":"ACTIVE","BRANCH":a.branch,
           "LAST_VERIFIED_HEAD":a.subject_head,"INTENT_REVISION":"2","CHANGE_RISK":"HIGH"}
   calls=[issue,{"permission":"admin"},{"sha":a.subject_head}]
   class CP:returncode=0;stdout="PASS"
   with patch.object(m.ext,"_exec_path",side_effect=[Path("/usr/bin/gh"),Path("/fixed/signer")]), patch.object(m.ext,"_run_json",side_effect=calls), patch.object(m.ext,"_fields",return_value=fields), patch.object(m.ext,"_canonical_execution_profile",return_value={}), patch.object(m.subprocess,"run",return_value=CP()) as run:
    self.assertEqual(m.authorize(a),0)
    cmd=run.call_args.args[0];self.assertEqual(cmd[0],"/fixed/signer")
    self.assertIn(str(a.request_json),cmd)
 def test_medium_packet_is_denied_before_signer(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d))
   issue={"state":"open","title":"[AI Work] prod","body":"x","user":{"login":"owner"}}
   fields={"TARGET_REPO":a.repository,"WORKSTREAM":a.workstream,"STATUS":"ACTIVE","BRANCH":a.branch,
           "LAST_VERIFIED_HEAD":a.subject_head,"INTENT_REVISION":"2","CHANGE_RISK":"MEDIUM"}
   with patch.object(m.ext,"_exec_path",side_effect=[Path("/usr/bin/gh"),Path("/fixed/signer")]), patch.object(m.ext,"_run_json",side_effect=[issue,{"permission":"admin"}]), patch.object(m.ext,"_fields",return_value=fields), self.assertRaises(m.Error):
    m.authorize(a)

if __name__=="__main__":unittest.main()
