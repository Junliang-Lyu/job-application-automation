import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from discovery_manager import normalize_options, outcome
class DiscoveryTests(unittest.TestCase):
    def test_options(self):
        d=normalize_options({'keywords':'backend engineer\nAI engineer\nbackend engineer','location':'Dallas','limit':6,'source':'linkedin'})
        self.assertEqual(d['keywords'],['backend engineer','AI engineer'])
        self.assertEqual(d['limit'],6)
    def test_bad_inputs(self):
        for d in [{'limit':100},{'limit':True},{'source':'shell'},{'location':''},{'keywords':'x'*201},{'keywords':['x']}]:
            with self.subTest(d=d),self.assertRaises(ValueError):normalize_options(d)
    def test_partial_is_not_total_failure(self):
        r=outcome({'fetched':2,'failures':2},{'statuses':{'ready_for_review':1,'not_recommended':1}})
        self.assertEqual(r['status'],'partial');self.assertEqual(r['ready'],1)
    def test_zero_and_preparation_failures(self):
        self.assertEqual(outcome({'fetched':0,'failures':1},{})['status'],'blocked')
        self.assertEqual(outcome({'fetched':2,'failures':0},{'statuses':{'analysis_failed':2}})['status'],'partial')
if __name__=='__main__':unittest.main()

class ManagerProcessTests(unittest.TestCase):
    def test_single_task_and_no_shell(self):
        from unittest.mock import patch,MagicMock
        from types import SimpleNamespace
        from discovery_manager import DiscoveryManager
        with tempfile.TemporaryDirectory() as tmp:
            manager=DiscoveryManager(SimpleNamespace(root=Path(tmp)))
            process=MagicMock();process.poll.return_value=None
            with patch('discovery_manager.subprocess.Popen',return_value=process) as launch:
                manager.start({'keywords':'AI engineer','location':'Dallas','limit':2})
                with self.assertRaises(ValueError):manager.start({})
                self.assertEqual(launch.call_count,1)
                self.assertIsInstance(launch.call_args.args[0],list)
                self.assertNotIn('shell',launch.call_args.kwargs)
                request=Path(launch.call_args.args[0][-1])
                self.assertEqual(__import__('json').loads(request.read_text())['keywords'],['AI engineer'])
    def test_stale_state_after_server_restart(self):
        from types import SimpleNamespace
        from discovery_manager import DiscoveryManager
        with tempfile.TemporaryDirectory() as tmp:
            manager=DiscoveryManager(SimpleNamespace(root=Path(tmp)))
            manager.base.mkdir(parents=True)
            manager.state.write_text('{"status":"searching"}')
            self.assertEqual(manager.status()['status'],'error')

class WorkerTests(unittest.TestCase):
    def test_partial_fetch_still_prepares_and_preserves_counts(self):
        import json
        from unittest.mock import patch
        from types import SimpleNamespace
        from discovery_manager import run
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'batch';folder.mkdir()
            request=folder/'request.json';request.write_text(json.dumps(dict(mode='search',keywords=['backend'],limit=4,location='Dallas',source='linkedin')))
            def process(cmd,**kwargs):
                if any('fetch_jobs_browser.py' in str(x) for x in cmd):
                    output=Path(cmd[cmd.index('--output')+1]);output.write_text('[{"status":"needs_human","error":"login_required"}]')
                    output.with_suffix('.summary.json').write_text('{"fetched":2,"failures":1}')
                    return SimpleNamespace(returncode=2)
                if any('prepare_batch.py' in str(x) for x in cmd):
                    (folder/'summary.json').write_text('{"statuses":{"ready_for_review":1,"not_recommended":1}}')
                    return SimpleNamespace(returncode=2)
                return SimpleNamespace(returncode=0)
            with patch('discovery_manager.subprocess.run',side_effect=process),patch('manual_workbench.Workbench.sync_materials'):
                run(request)
            state=json.loads((Path(tmp)/'status.json').read_text(encoding='utf-8'))
            self.assertEqual(state['status'],'partial');self.assertEqual(state['ready'],1)
            self.assertIn('login_required',state['block_reasons'])
