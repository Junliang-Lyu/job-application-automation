import json,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

class HandoffUiTests(unittest.TestCase):
    def test_buttons_make_explicit_handoff_without_starting_fill(self):
        html=(Path(__file__).resolve().parents[1]/'scripts/manual_workbench.html').read_text(encoding='utf-8').replace('__TOKEN__','fixture')
        job={'key':'linkedin:123','company':'Acme','title':'Engineer','location':'Dallas','source':'LinkedIn','url':'https://www.linkedin.com/jobs/view/123/','official':'','status':'pending','score':85,'ready':True,'reason':'Fixture','note':'','jd':'Build services','resume':'Resume.pdf','cover_pdf':'Letter.pdf','materials':{'folder':'C:/fixture','files':{}},'letter':''}
        requests=[];posts=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page()
            def route(req):
                path=req.request.url.split('8765')[-1]
                requests.append(path)
                if req.request.method=='POST':posts.append(path)
                if path=='/':return req.fulfill(body=html,content_type='text/html; charset=utf-8')
                if path=='/api/jobs':value={'jobs':[job],'ready_count':1,'queues':1,'answers':[],'projects':[]}
                elif path=='/api/discovery':value={'status':'idle','bridge_connected':False,'message':'fixture'}
                elif path=='/api/codex-handoff':value={'prompt':'Read C:/fixture/task.txt; stop before Submit','path':'C:/fixture/task.txt','message':'粘贴到 Codex 新 chat 后开始'}
                else:value={'status':'idle'}
                req.fulfill(body=json.dumps(value),content_type='application/json')
            page.route('**/*',route)
            page.goto('http://127.0.0.1:8765/')
            handoff=page.get_by_role('button',name='交给 Codex Computer Use · 复制任务',exact=True)
            handoff.wait_for()
            self.assertTrue(page.get_by_role('button',name='暂不可填写 · 缺少支持的申请链接',exact=True).is_disabled())
            # A connected old extension must fail immediately, rather than wait for a missing handler.
            page.evaluate("document.documentElement.dataset.jobsearchBridge='connected'")
            page.get_by_text('查找 Greenhouse / Lever 链接（可选）',exact=True).click()
            resolve=page.get_by_role('button',name='查找脚本支持的申请链接',exact=True)
            resolve.click()
            page.get_by_text('当前扩展尚未加载官网识别功能：请重新加载 Job Search 本地搜岗连接，再刷新工作台；现在可直接复制 Computer Use 任务',exact=True).wait_for()
            self.assertTrue(resolve.is_enabled())
            page.get_by_placeholder('可选：粘贴申请页链接；留空则由 Codex 从来源页继续').fill('https://jobright.ai/jobs/info/123')
            page.get_by_text('此入口不支持脚本填写，请点击“交给 Codex Computer Use · 复制任务”。',exact=True).wait_for()
            self.assertTrue(page.get_by_role('button',name='交给 Codex 填写 · 脚本不支持',exact=True).is_disabled())
            handoff.click()
            page.get_by_text('Read C:/fixture/task.txt; stop before Submit',exact=True).wait_for()
            self.assertIn('/api/codex-handoff',requests)
            self.assertNotIn('/api/assist',posts)
            browser.close()
