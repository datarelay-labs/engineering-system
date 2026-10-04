import importlib.util,json,tempfile,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("prod_signer",ROOT/"tools"/"trusted_production_write_signer.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class A:pass
class ProductionSignerTests(unittest.TestCase):
 def test_non_root_denied(self):
  with patch.object(m.os,"geteuid",return_value=1000),self.assertRaises(m.SignerError):m.issue(A())
 def test_exact_request_is_signed_without_execution(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d); key=d/"k";pub=d/"p";req=d/"r.json";bo=d/"b";do=d/"d"
   subprocess.run(["/usr/bin/openssl","genpkey","-algorithm","ED25519","-out",str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   subprocess.run(["/usr/bin/openssl","pkey","-in",str(key),"-pubout","-out",str(pub)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   req.write_text(json.dumps({"operation":"atlas-production-rollout","target":"prod-atlas","candidate_head":"a"*40}))
   a=A();a.root=ROOT;a.request_json=req;a.repository="datarelay-labs/engineering-system";a.workstream="trusted-production-write-bootstrap";a.branch="feat/trusted-production-write-bootstrap";a.subject_head="a"*40;a.intent_revision=2;a.session_id="s1";a.dispatch_id="d1";a.authority_permission="admin";a.ttl_seconds=120;a.binding_out=bo;a.dispatch_out=do
   old=(m.KEY,m.PUB,m.SKILLS);m.KEY=key;m.PUB=pub;m.SKILLS=ROOT/"tools"/"skills-contract.py"
   try:
    key.chmod(0o600); pub.chmod(0o644)
    real_os_stat=m.os.stat
    class V:
     def __init__(self,real,mode):self.st_uid=0;self.st_mode=mode
    def fake_os_stat(path,*args,**kwargs):
     real=real_os_stat(path,*args,**kwargs)
     if Path(path)==m.KEY:return V(real,0o100600)
     if Path(path)==m.PUB:return V(real,0o100644)
     return real
    with patch.object(m.os,"geteuid",return_value=0), patch.object(m.os,"stat",side_effect=fake_os_stat), patch.object(m,"_load") as load:
     real=importlib.util.spec_from_file_location("real_skills",ROOT/"tools"/"skills-contract.py")
     skills=importlib.util.module_from_spec(real);sys.modules[real.name]=skills;real.loader.exec_module(skills);load.return_value=skills
     self.assertEqual(m.issue(a),0)
     a.authority_basis="production_approver_policy";a.authority_permission="production_approver";a.binding_out=d/"b2";a.dispatch_out=d/"d2"
     self.assertEqual(m.issue(a),0)
    binding=json.loads(bo.read_text());dispatch=json.loads(do.read_text());approver=json.loads((d/"b2").read_text())
    self.assertEqual(binding["profile"],"production_write");self.assertEqual(binding["authority_basis"],"collaborator_permission");self.assertEqual(dispatch["tool_id"],"shell.production_write")
    self.assertEqual(approver["authority_basis"],"production_approver_policy");self.assertEqual(approver["authority_permission"],"production_approver")
    self.assertEqual(dispatch["classes"],["destructive","external_read","network","production_write","shell"])
   finally:m.KEY,m.PUB,m.SKILLS=old
if __name__=="__main__":unittest.main()
