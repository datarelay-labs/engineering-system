import shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class InstalledLayoutTests(unittest.TestCase):
 def test_coordinator_imports_from_installed_layout(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d)
   shutil.copy2(ROOT/"tools"/"trusted_production_write_coordinator.py",d/"trusted-production-write-coordinator")
   shutil.copy2(ROOT/"tools"/"trusted_external_write_coordinator.py",d/"trusted_external_write_coordinator.py")
   shutil.copy2(ROOT/"tools"/"execution_profile.py",d/"execution_profile.py")
   cp=subprocess.run(["python3",str(d/"trusted-production-write-coordinator"),"--help"],cwd="/",env={"PATH":"/usr/bin:/bin","PYTHONPATH":""},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   self.assertEqual(cp.returncode,0,cp.stderr)

if __name__=="__main__":unittest.main()
