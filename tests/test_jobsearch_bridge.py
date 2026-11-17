import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jobsearch_bridge import JobSearchBridge,validate_records
class BridgeTests(unittest.TestCase):
    def test_reject_wrong_host_and_fake_success(self):
        for row in [{'sourceUrl':'https://evil.com/jobs/view/123','status':'fetched','text':'x'*300}, {'sourceUrl':'https://www.linkedin.com/jobs/view/123','status':'fetched','text':'short'}]:
            with self.assertRaises(ValueError):validate_records([row],2)
    def test_connection_required_and_replay(self):
        desk=Mock();desk.records.return_value={};manager=Mock();manager.status.return_value={'status':'idle'}
        bridge=JobSearchBridge(desk,manager)
        with self.assertRaises(ValueError):bridge.start({})
        bridge.poll('client1');bridge.start({'keywords':'backend'})
        task=bridge.poll('client1')['task'];self.assertIsNotNone(task)
        with self.assertRaises(ValueError):bridge.complete({'id':'wrong','client':'client1','records':[]})
        bridge.complete({'id':task['id'],'client':'client1','records':[]})
        with self.assertRaises(ValueError):bridge.complete({'id':task['id'],'client':'client1','records':[]})
    def test_other_client_cannot_take_task(self):
        desk=Mock();desk.records.return_value={};manager=Mock();manager.status.return_value={'status':'idle'}
        b=JobSearchBridge(desk,manager);b.poll('a');b.start({'keywords':'backend'});b.poll('a')
        self.assertIsNone(b.poll('b')['task'])

    def test_keyword_queries_and_progress_are_bound_to_task(self):
        from urllib.parse import urlsplit,parse_qs
        desk=Mock();desk.records.return_value={};manager=Mock();manager.status.return_value={'status':'idle'}
        b=JobSearchBridge(desk,manager);b.poll('a');b.start({'source':'both','keywords':'backend engineer\nAI engineer\nnew grad'})
        task=b.poll('a')['task'];self.assertEqual(len(task['searches']),6)
        self.assertEqual([r['source'] for r in task['searches'][:2]],['LinkedIn','Handshake'])
        for r in task['searches']:
            if r['source']=='Handshake':self.assertEqual(parse_qs(urlsplit(r['url']).query)['query'],[r['keyword']])
        data={'id':task['id'],'client':'a','report':{'queries':[{'source':'Handshake','keyword':'backend engineer','duplicates':3,'fetched':1}]}}
        b.progress(data);self.assertEqual(b.status()['search_report']['queries'][0]['duplicates'],3)
        with self.assertRaises(ValueError):b.progress(dict(data,client='b'))
        with self.assertRaises(ValueError):b.progress(dict(data,report={'queries':[{'source':'Handshake','keyword':'backend engineer','duplicates':-1}]}))
        with self.assertRaises(ValueError):b.progress(dict(data,report={'queries':[{'source':'Handshake','keyword':'injected'}]}))

    def test_history_outside_queue_and_pending_jobs_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'applications').mkdir()
            (root/'applications/applications.csv').write_text('Job URL,Status\nhttps://smu.joinhandshake.com/jobs/99,Applied\nhttps://www.linkedin.com/jobs/view/title-88/,Applied - verify\n',encoding='utf-8')
            desk=Mock();desk.root=root;desk.records.return_value={'pending':{'sourceUrl':'https://smu.joinhandshake.com/job-search/77','status':'ready_for_review'}}
            manager=Mock();manager.status.return_value={'status':'idle'}
            b=JobSearchBridge(desk,manager);b.poll('a');b.start({'keywords':'backend','source':'handshake'})
            self.assertEqual(set(b.task['excluded']),{'handshake:99','linkedin:88','handshake:77'})
            self.assertEqual(b.task['max_pages'],3)

    def test_rotation_advances_only_attempted_keywords(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            desk=Mock();desk.records.return_value={};manager=Mock();manager.status.return_value={'status':'idle'}
            state=Path(tmp)/'rotation.json'
            with patch('jobsearch_bridge.STATE',state),patch('jobsearch_bridge.load_queries',return_value=['a','b','c','d','e']):
                b=JobSearchBridge(desk,manager);b.poll('client');b.start({'source':'handshake'})
                task=b.poll('client')['task'];self.assertEqual([r['keyword'] for r in task['searches']],['a','b','c'])
                b.complete({'id':task['id'],'client':'client','records':[],'attempted':['a','b']})
                b.start({'source':'handshake'})
                self.assertEqual([r['keyword'] for r in b.task['searches']],['c','d','e'])

class ExclusivePortTests(unittest.TestCase):
    def test_second_workbench_cannot_bind_same_port(self):
        from manual_workbench import WorkbenchServer
        from http.server import BaseHTTPRequestHandler
        first=WorkbenchServer(('127.0.0.1',0),BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):WorkbenchServer(('127.0.0.1',first.server_port),BaseHTTPRequestHandler)
        finally:first.server_close()
