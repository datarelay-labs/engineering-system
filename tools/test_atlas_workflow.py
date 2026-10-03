import json,os,subprocess,tempfile,unittest
from pathlib import Path
TOOL=Path(__file__).with_name("atlas-workflow.py")
class Tests(unittest.TestCase):
 def invoke(self,*args,env=None):
  e=os.environ.copy();e.pop("ATLAS_OPERATOR_ARGV_JSON",None)
  if env:e.update(env)
  return subprocess.run(["python3",str(TOOL),*args],env=e,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 def test_start_falls_back_when_atlas_unavailable(self):
  r=self.invoke("start","--repository","datarelay-labs/x","--workstream","w");self.assertEqual(r.returncode,0);self.assertEqual(json.loads(r.stdout)["state"],"ATLAS_UNAVAILABLE")
 def test_start_and_finish_use_bounded_operator_argv(self):
  with tempfile.TemporaryDirectory() as d:
   fake=Path(d)/"fake.py";log=Path(d)/"log"
   fake.write_text("import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(Path(sys.argv[1]).read_text()+' '+json.dumps(sys.argv[2:]) if Path(sys.argv[1]).exists() else json.dumps(sys.argv[2:]))\nprint(json.dumps({'state':'OK'}))\n")
   env={"ATLAS_OPERATOR_ARGV_JSON":json.dumps(["python3",str(fake),str(log)])}
   r=self.invoke("start","--repository","datarelay-labs/x","--workstream","w",env=env);self.assertEqual(r.returncode,0)
   r=self.invoke("finish","--project-id","x","--repository","datarelay-labs/x","--workstream","w","--candidate-class","LESSON_LEARNED","--content","bounded lesson","--important-expected","1","--important-recalled","1","--injected-context-bytes","100","--first-pass-success",env=env);self.assertEqual(r.returncode,0)
   text=log.read_text();self.assertIn("task-context",text);self.assertIn("memory-candidates",text);self.assertIn("memory-effectiveness",text)
if __name__=="__main__":unittest.main()
