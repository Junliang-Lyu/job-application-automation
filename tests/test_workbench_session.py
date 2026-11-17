import sys,tempfile,threading,unittest,json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from manual_workbench import Workbench,WorkbenchServer,handler
class SessionTests(unittest.TestCase):
 def test_stale_token_and_cross_origin(self):
  with tempfile.TemporaryDirectory() as tmp:
   server=WorkbenchServer(('127.0.0.1',0),handler(Workbench(Path(tmp),[]),'current'))
   threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
   try:
    with self.assertRaises(HTTPError) as caught:urlopen(Request(url+'/api/bridge/poll',data=b'{"client":"test"}',headers={'Origin':url,'X-Workbench-Token':'old'}))
    self.assertEqual(json.load(caught.exception)['code'],'session_expired')
    with self.assertRaises(HTTPError):urlopen(Request(url+'/api/session',headers={'X-Workbench-Session':'refresh','Origin':'https://evil.example'}))
    with self.assertRaises(HTTPError):urlopen(url+'/api/session')
    token=json.load(urlopen(Request(url+'/api/session',headers={'X-Workbench-Session':'refresh'})))['token']
    result=json.load(urlopen(Request(url+'/api/bridge/poll',data=b'{"client":"test"}',headers={'Origin':url,'X-Workbench-Token':token})))
    self.assertIsNone(result['task'])
   finally:server.shutdown();server.server_close()
