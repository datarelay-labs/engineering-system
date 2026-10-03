import importlib.util, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("boundary_admin",ROOT/"tools"/"trusted_boundary_admin.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class Args:
    def __init__(self,source=None): self.source=source

class BoundaryAdminTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        base=Path(self.t.name); self.etc=base/"etc"; self.lib=base/"lib"; self.source=base/"src"; (self.source/"tools").mkdir(parents=True)
        for name in dict.fromkeys((*m.INSTALLS, *m.IMPORTS)): (self.source/"tools"/name).write_text("#!/usr/bin/env python3\n")
        self.old=(m.ETC,m.LIB,m.KEY,m.PUB,m.REPLAY)
        m.ETC=self.etc; m.LIB=self.lib; m.KEY=self.etc/"skills-trust-anchor.key"; m.PUB=self.etc/"skills-trust-anchor.pub"; m.REPLAY=self.etc/"skills-replay-state"
        self.addCleanup(self.restore)
    def restore(self): m.ETC,m.LIB,m.KEY,m.PUB,m.REPLAY=self.old
    def test_non_root_fails(self):
        with patch.object(m.os,"geteuid",return_value=1000), self.assertRaises(m.BoundaryError): m.verify(Args())
    def test_install_verify_remove(self):
        real_run=m.subprocess.run
        def fake_run(cmd,**kw):
            if "genpkey" in cmd: m.KEY.write_text("private")
            elif "-pubout" in cmd: m.PUB.write_text("public")
            class R:returncode=0
            return R()
        with patch.object(m.os,"geteuid",return_value=0), patch.object(m.os,"chown",return_value=None), patch.object(m.subprocess,"run",side_effect=fake_run), patch.object(m,"_secure_dir",return_value=None), patch.object(m,"_secure_file",return_value=None):
            m.install(Args(self.source)); self.assertTrue(m.KEY.exists()); self.assertEqual(m.KEY.stat().st_mode&0o777,0o600)
            self.assertTrue((m.LIB/"trusted-external-write-coordinator").is_file())
            self.assertTrue((m.LIB/"trusted_external_write_coordinator.py").is_file())
            self.assertEqual((m.LIB/"trusted-external-write-coordinator").stat().st_mode&0o777,0o755)
            self.assertEqual((m.LIB/"trusted_external_write_coordinator.py").stat().st_mode&0o777,0o644)
            m.verify(Args()); m.remove(Args()); self.assertFalse(m.KEY.exists())
    def test_existing_anchor_fails_closed(self):
        self.etc.mkdir(); m.KEY.write_text("x")
        with patch.object(m.os,"geteuid",return_value=0), self.assertRaises(m.BoundaryError): m.install(Args(self.source))
if __name__=="__main__":unittest.main()
