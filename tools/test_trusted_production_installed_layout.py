import shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class InstalledLayoutTests(unittest.TestCase):
    def test_coordinators_execute_from_isolated_installed_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed=Path(tmp)
            # Preserve both the executable compatibility surface and the importable module.
            shutil.copy2(ROOT/"tools"/"trusted_external_write_coordinator.py", installed/"trusted-external-write-coordinator")
            shutil.copy2(ROOT/"tools"/"trusted_external_write_coordinator.py", installed/"trusted_external_write_coordinator.py")
            shutil.copy2(ROOT/"tools"/"trusted_production_write_coordinator.py", installed/"trusted-production-write-coordinator")
            shutil.copy2(ROOT/"tools"/"execution_profile.py", installed/"execution_profile.py")
            shutil.copy2(ROOT/"tools"/"skills-contract.py", installed/"skills-contract.py")
            shutil.copy2(ROOT/"tools"/"work_packet_authority.py", installed/"work_packet_authority.py")
            shutil.copy2(ROOT/"tools"/"trusted_production_write_signer.py", installed/"trusted-production-write-signer")
            env={"PATH":"/usr/bin:/bin","PYTHONPATH":""}
            for tool in ("trusted-external-write-coordinator","trusted-production-write-coordinator","trusted-production-write-signer"):
                cp=subprocess.run(
                    ["python3",str(installed/tool),"--help"],
                    cwd="/",env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                )
                self.assertEqual(cp.returncode,0,cp.stderr)

if __name__=="__main__":
    unittest.main()
