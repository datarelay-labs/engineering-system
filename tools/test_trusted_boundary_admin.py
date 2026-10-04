import importlib.util, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("boundary_admin",ROOT/"tools"/"trusted_boundary_admin.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class Args:
    def __init__(self,source=None,policy=None,require=False):
        self.source=source
        self.production_approver_policy=policy
        self.require_production_approvers=require

class BoundaryAdminTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        base=Path(self.t.name); self.etc=base/"etc"; self.lib=base/"lib"; self.source=base/"src"; (self.source/"tools").mkdir(parents=True)
        for name in dict.fromkeys((*m.INSTALLS, *m.IMPORTS)): (self.source/"tools"/name).write_text("#!/usr/bin/env python3\n")
        self.old=(m.ETC,m.LIB,m.KEY,m.PUB,m.REPLAY,m.APPROVERS)
        m.ETC=self.etc; m.LIB=self.lib; m.KEY=self.etc/"skills-trust-anchor.key"; m.PUB=self.etc/"skills-trust-anchor.pub"; m.REPLAY=self.etc/"skills-replay-state"; m.APPROVERS=self.etc/"production-approvers.json"
        self.addCleanup(self.restore)
    def restore(self): m.ETC,m.LIB,m.KEY,m.PUB,m.REPLAY,m.APPROVERS=self.old
    def policy(self):
        path=Path(self.t.name)/"approvers.json"
        approval={"repository":"datarelay-labs/datarelay-atlas","issue_id":307,"workstream":"prod-web-recovery-rollout-7e9ff06","branch":"ops/prod-web-recovery-rollout-7e9ff06","subject_head":"a"*40,"intent_revision":1,"session_id":"rollout-307","dispatch_id":"rollout-307-stage","packet_sha256":"b"*64,"request_sha256":"c"*64,"approved_by":"RickLee-kr"}
        path.write_text(json.dumps({"schema_version":1,"kind":"trusted_production_approver_policy","approvals":[approval]}))
        return path
    def fake_run(self,cmd,**kw):
        if "genpkey" in cmd: m.KEY.write_text("private")
        elif "-pubout" in cmd: m.PUB.write_text("public")
        class R:returncode=0
        return R()
    def patches(self):
        return (
            patch.object(m.os,"geteuid",return_value=0),
            patch.object(m.os,"chown",return_value=None),
            patch.object(m.subprocess,"run",side_effect=self.fake_run),
            patch.object(m,"_secure_dir",return_value=None),
            patch.object(m,"_secure_file",return_value=None),
        )
    def test_non_root_fails(self):
        with patch.object(m.os,"geteuid",return_value=1000), self.assertRaises(m.BoundaryError): m.verify(Args())
    def test_install_verify_remove(self):
        p=self.patches()
        with p[0],p[1],p[2],p[3],p[4]:
            m.install(Args(self.source)); self.assertTrue(m.KEY.exists()); self.assertEqual(m.KEY.stat().st_mode&0o777,0o600)
            self.assertTrue((m.LIB/"trusted-external-write-coordinator").is_file())
            self.assertTrue((m.LIB/"production_approver_policy.py").is_file())
            m.verify(Args()); m.remove(Args()); self.assertFalse(m.KEY.exists())
    def test_existing_anchor_fails_closed(self):
        self.etc.mkdir(); m.KEY.write_text("x")
        with patch.object(m.os,"geteuid",return_value=0), self.assertRaises(m.BoundaryError): m.install(Args(self.source))
    def test_upgrade_preserves_anchor_and_installs_policy(self):
        p=self.patches()
        with p[0],p[1],p[2],p[3],p[4]:
            m.install(Args(self.source))
            key_before=m.KEY.read_bytes(); pub_before=m.PUB.read_bytes()
            (self.source/"tools"/"trusted_production_write_coordinator.py").write_text("#!/usr/bin/env python3\n# upgraded\n")
            m.upgrade(Args(self.source,self.policy(),True))
            self.assertEqual(m.KEY.read_bytes(),key_before); self.assertEqual(m.PUB.read_bytes(),pub_before)
            self.assertTrue(m.APPROVERS.is_file()); self.assertEqual(m.APPROVERS.stat().st_mode&0o777,0o600)
            payload=json.loads(m.APPROVERS.read_text()); self.assertEqual(payload["approvals"][0]["approved_by"],"RickLee-kr"); self.assertEqual(payload["approvals"][0]["dispatch_id"],"rollout-307-stage")
            self.assertIn("upgraded",(m.LIB/"trusted-production-write-coordinator").read_text())
            m.verify(Args(require=True))
    def test_invalid_policy_fails_before_install_or_upgrade_mutation(self):
        invalid=Path(self.t.name)/"invalid-policy.json"; invalid.write_text("{}")
        with patch.object(m.os,"geteuid",return_value=0), self.assertRaisesRegex(m.BoundaryError,"policy"):
            m.install(Args(self.source,invalid))
        self.assertFalse(self.etc.exists())
        p=self.patches()
        with p[0],p[1],p[2],p[3],p[4]:
            m.install(Args(self.source))
            installed=m.LIB/"trusted-production-write-coordinator"
            before=installed.read_bytes()
            (self.source/"tools"/"trusted_production_write_coordinator.py").write_text("#!/usr/bin/env python3\n# must-not-land\n")
            with self.assertRaisesRegex(m.BoundaryError,"policy"):
                m.upgrade(Args(self.source,invalid))
            self.assertEqual(installed.read_bytes(),before)
    def test_verify_requires_policy_when_requested(self):
        p=self.patches()
        with p[0],p[1],p[2],p[3],p[4]:
            m.install(Args(self.source))
            with self.assertRaisesRegex(m.BoundaryError,"policy unavailable"): m.verify(Args(require=True))

if __name__=="__main__":unittest.main()
