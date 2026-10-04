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
 def fields(self,a,risk="HIGH"):
  return {"TARGET_REPO":a.repository,"WORKSTREAM":a.workstream,"STATUS":"ACTIVE","BRANCH":a.branch,
          "LAST_VERIFIED_HEAD":a.subject_head,"INTENT_REVISION":str(a.intent_revision),"CHANGE_RISK":risk,
          "EXECUTION_PROFILE":"datarelay-managed","EXECUTION_PROFILE_REVISION":"2"}
 def issue(self,body="x",login="owner"):
  return {"number":268,"state":"open","title":"[AI Work] prod","body":body,"user":{"login":login}}
 def approval(self,a,body="x",request=None,approved_by="owner"):
  request={} if request is None else request
  return ({
   "repository":a.repository,"issue_id":int(a.issue_id),"workstream":a.workstream,"branch":a.branch,
   "subject_head":a.subject_head,"intent_revision":a.intent_revision,"session_id":a.session_id,
   "dispatch_id":a.dispatch_id,"packet_sha256":m.packet_sha256(body),
   "request_sha256":m.canonical_json_sha256(request),"approved_by":approved_by,
  },)
 def test_exact_active_high_packet_invokes_fixed_signer_only(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d));issue=self.issue();fields=self.fields(a)
   calls=[issue,{"permission":"admin"},{"sha":a.subject_head}]
   class CP:returncode=0;stdout="PASS"
   with patch.object(m,"_direct_root_without_operator",return_value=False), patch.object(m.ext,"_exec_path",side_effect=[Path("/usr/bin/gh"),Path("/fixed/signer")]), patch.object(m.ext,"_run_json",side_effect=calls), patch.object(m.ext,"_fields",return_value=fields), patch.object(m.ext,"_canonical_execution_profile",return_value={}), patch.object(m.ext,"packet_authority",return_value=(None,[])), patch.object(m.subprocess,"run",return_value=CP()) as run:
    self.assertEqual(m.authorize(a),0)
    cmd=run.call_args.args[0];self.assertEqual(cmd[0],"/fixed/signer")
    self.assertIn(str(a.request_json),cmd);self.assertNotIn("--authority-basis",cmd)
 def test_medium_packet_is_denied_before_signer(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d));issue=self.issue();fields=self.fields(a,"MEDIUM")
   with patch.object(m,"_direct_root_without_operator",return_value=False), patch.object(m.ext,"_exec_path",side_effect=[Path("/usr/bin/gh"),Path("/fixed/signer")]), patch.object(m.ext,"_run_json",side_effect=[issue,{"permission":"admin"}]), patch.object(m.ext,"_fields",return_value=fields), self.assertRaises(m.Error):
    m.authorize(a)
 def test_root_only_fallback_requires_exact_root_admin_approval(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d));fields=self.fields(a);issue=self.issue(login="different-writer")
   public=[{"private":False,"visibility":"public"},issue,{"sha":a.subject_head},{"sha":a.subject_head}]
   class CP:returncode=0;stdout="PASS"
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=self.approval(a)), patch.object(m,"_public_json",side_effect=public), patch.object(m.ext,"_fields",return_value=fields), patch.object(m,"_public_execution_profile",return_value={}), patch.object(m.ext,"packet_authority",return_value=(None,[])), patch.object(m.ext,"_exec_path",return_value=Path("/fixed/signer")), patch.object(m.subprocess,"run",return_value=CP()) as run:
    self.assertEqual(m.authorize(a),0)
    cmd=run.call_args.args[0]
    self.assertIn("--authority-basis",cmd);self.assertIn("production_approver_policy",cmd)
    self.assertIn("--authority-permission",cmd);self.assertIn("production_approver",cmd)
 def test_root_only_fallback_rejects_mutated_packet_request_or_dispatch(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d));policy=self.approval(a)
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",side_effect=m.Error("production approver policy unavailable")), self.assertRaisesRegex(m.Error,"policy unavailable"):
    m.authorize(a)
   public=[{"private":False,"visibility":"public"},self.issue(body="edited")]
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=policy), patch.object(m,"_public_json",side_effect=public), self.assertRaisesRegex(m.Error,"exact root-admin production approval"):
    m.authorize(a)
   a.request_json.write_text('{"changed":true}')
   public=[{"private":False,"visibility":"public"},self.issue()]
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=policy), patch.object(m,"_public_json",side_effect=public), self.assertRaisesRegex(m.Error,"exact root-admin production approval"):
    m.authorize(a)
   a.request_json.write_text("{}");a.dispatch_id="different"
   public=[{"private":False,"visibility":"public"},self.issue()]
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=policy), patch.object(m,"_public_json",side_effect=public), self.assertRaisesRegex(m.Error,"exact root-admin production approval"):
    m.authorize(a)
 def test_public_tls_context_ignores_caller_trust_store_environment(self):
  class Context:
   def __init__(self): self.cafile=None; self.check_hostname=False; self.verify_mode=None; self.minimum_version=None
   def load_verify_locations(self,*,cafile): self.cafile=cafile
  context=Context()
  with patch.dict(m.os.environ,{"SSL_CERT_FILE":"/untrusted/ca.pem","SSL_CERT_DIR":"/untrusted/certs"},clear=False), patch.object(m,"_trusted_ca_bundle",return_value=Path("/fixed/admin-ca.pem")), patch.object(m.ssl,"SSLContext",return_value=context) as ctor:
   self.assertIs(m._public_ssl_context(),context)
  ctor.assert_called_once_with(m.ssl.PROTOCOL_TLS_CLIENT)
  self.assertEqual(context.cafile,"/fixed/admin-ca.pem")
  self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,m.ssl.CERT_REQUIRED);self.assertEqual(context.minimum_version,m.ssl.TLSVersion.TLSv1_2)
 def test_root_only_fallback_rejects_private_repo_and_head_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.args(Path(d));fields=self.fields(a);policy=self.approval(a)
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=policy), patch.object(m,"_public_json",side_effect=[{"private":True,"visibility":"private"}]), self.assertRaisesRegex(m.Error,"public repository"):
    m.authorize(a)
   public=[{"private":False,"visibility":"public"},self.issue(),{"sha":"b"*40}]
   with patch.object(m,"_direct_root_without_operator",return_value=True), patch.object(m,"_load_approver_policy",return_value=policy), patch.object(m,"_public_json",side_effect=public), patch.object(m.ext,"_fields",return_value=fields), self.assertRaisesRegex(m.Error,"branch HEAD mismatch"):
    m.authorize(a)

if __name__=="__main__":unittest.main()
