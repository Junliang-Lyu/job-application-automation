"""Bounded, user-started discovery jobs. No shell interpolation or ATS submissions."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from datetime import datetime

PORT=18767
ACTIVE={'starting','searching','preparing','login'}

def replace_retry(tmp,target,tries=20):
    """Windows refuses os.replace while the workbench is reading the target; wait briefly instead of failing the run."""
    import time
    for i in range(tries):
        try:return tmp.replace(target)
        except PermissionError:
            if i==tries-1:raise
            time.sleep(0.25)

def normalize_options(data):
    keywords=data.get('keywords','')
    location=data.get('location','United States')
    limit=data.get('limit',6)
    source=data.get('source','linkedin')
    if not isinstance(keywords,str) or len(keywords)>600:raise ValueError('关键词格式无效')
    queries=list(dict.fromkeys(x.strip() for x in keywords.splitlines() if x.strip()))
    if len(queries)>3 or any(len(q)>200 for q in queries):raise ValueError('每轮最多 3 个关键词，每行一个')
    if not isinstance(location,str) or not location.strip() or len(location)>120:raise ValueError('请输入地区')
    if type(limit)!=int or not 1<=limit<=12:raise ValueError('每轮最多 1–12 份 JD')
    if source not in ('linkedin','handshake','both'):raise ValueError('未知来源')
    return dict(keywords=queries,location=location.strip(),limit=limit,source=source)

def outcome(discovery, preparation):
    statuses=preparation.get('statuses',{})
    failed=sum(statuses.get(s,0) for s in ('analysis_failed','materials_failed'))
    fetched=discovery.get('fetched',0); blocked=discovery.get('failures',0)
    state='blocked' if not fetched and blocked else ('partial' if blocked or failed else 'complete')
    direct=statuses.get('ready_for_review',0); review=statuses.get('needs_qualification_review',0)
    # "ready" stays the total with materials generated; the message separates what can be applied to now.
    return dict(status=state,fetched=fetched,blocked=blocked,ready=direct+review,ready_direct=direct,needs_review=review,preparation_failed=failed,
                message=f"取得 {fetched} 份 JD；可直接投 {direct} 份，资格待你核对 {review} 份（两类材料都已生成）；抓取失败 {blocked} 项；材料失败 {failed} 项")

class DiscoveryManager:
    def __init__(self,workbench):
        self.workbench=workbench;self.process=None
        self.base=workbench.root/'tmp/workbench-discovery';self.state=self.base/'status.json'
    def status(self):
        result=json.loads(self.state.read_text(encoding='utf-8')) if self.state.exists() else {'status':'idle','message':'尚未开始搜岗'}
        if self.process and self.process.poll() is not None and result.get('status') in ACTIVE:
            result.update(status='error',message='任务意外结束；已有结果保留，请查看运行记录')
        elif not self.process and result.get('status') in ACTIVE:
            with socket.socket() as probe:
                try:probe.bind(('127.0.0.1',PORT))
                except OSError:pass
                else:result.update(status='error',message='上次任务已停止；已有结果保留')
        return result
    def start_import(self,records,options):
        return self.start(dict(options,keywords='\n'.join(options['keywords']),mode='import'),records=records)
    def start(self,data,records=None):
        options=normalize_options(data)
        mode=data.get('mode','search')
        if mode not in ('search','login','import') or (mode=='import' and records is None):raise ValueError('未知任务类型')
        if self.process and self.process.poll() is None:raise ValueError('已有搜岗/登录任务运行，请先完成当前任务')
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',PORT))
            except OSError:raise ValueError('搜岗浏览器正在使用，请先关闭登录窗口')
        run=self.base/datetime.now().strftime('%Y%m%d-%H%M%S-%f');run.mkdir(parents=True)
        if records is not None:(run/'jobs.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
        request=run/'request.json';request.write_text(json.dumps(dict(options,mode=mode,search_report=data.get('search_report',{})),ensure_ascii=False),encoding='utf-8')
        self.state.write_text(json.dumps({'status':'starting','message':'正在准备抓取结果','run':str(run),'search_report':data.get('search_report',{})}),encoding='utf-8')
        with (run/'process.log').open('w',encoding='utf-8') as log:
            self.process=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).resolve()),str(request)],cwd=self.workbench.root,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return self.status()

def run(request):
    root=Path(__file__).resolve().parents[1];folder=request.parent
    state=folder.parent/'status.json';data=json.loads(request.read_text(encoding='utf-8'))
    def report(**values):
        tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps(dict(search_report=data.get('search_report',{}),**values,run=str(folder)),ensure_ascii=False),encoding='utf-8')
        replace_retry(tmp,state)
    lock=socket.socket()
    try:lock.bind(('127.0.0.1',PORT))
    except OSError:return
    try:
        if data['mode']=='login':
            report(status='login',message='请在独立搜岗 Edge 中登录 LinkedIn / Handshake；完成后关闭该窗口，再开始抓取')
            from playwright.sync_api import sync_playwright
            from runtime_config import launch_discovery_browser_context
            with sync_playwright() as pw:
                context=launch_discovery_browser_context(pw,headless=False)
                for url in ['https://www.linkedin.com/login','https://smu.joinhandshake.com/job-search']:
                    page=context.new_page()
                    try:page.goto(url,wait_until='domcontentloaded',timeout=20000)
                    except Exception:pass
                while context.pages:
                    try:context.pages[0].wait_for_timeout(1000)
                    except Exception:break
            report(status='complete',message='登录窗口已关闭，登录状态是否有效将在下次抓取时验证');return
        report(status='searching',message='脚本搜岗中：最多 3 个关键词，遇登录墙停止该来源；不操作投递窗口')
        output=folder/'jobs.json'
        if data['mode']=='import':
            rows=json.loads(output.read_text(encoding='utf-8'))
            discovery={'fetched':sum(x['status']=='fetched' for x in rows),'failures':sum(x['status'] not in ('fetched','search_empty') for x in rows)}
            output.with_suffix('.summary.json').write_text(json.dumps(discovery),encoding='utf-8')
        else:
            cmd=[sys.executable,'-X','utf8',str(root/'scripts/fetch_jobs_browser.py'),'--max-easy-apply','0',
                 '--max-all-ats',str(data['limit']),'--max-handshake',str(min(4,data['limit'])),
                 '--max-total',str(data['limit']),'--max-per-query','2','--max-queries','3',
                 '--location',data['location'],'--output',str(output)]
            if data['source']=='linkedin':cmd+=['--skip-handshake']
            if data['source']=='handshake':cmd+=['--skip-linkedin']
            for q in data['keywords']:cmd+=['--keyword',q]
            with (folder/'fetch.log').open('w',encoding='utf-8') as log:
                fetch=subprocess.run(cmd,cwd=root,stdout=log,stderr=log,timeout=360)
            summary=output.with_suffix('.summary.json')
            if not summary.exists():raise RuntimeError('搜岗未生成结果摘要，请查看 fetch.log')
            discovery=json.loads(summary.read_text(encoding='utf-8'))
        preparation={}
        if discovery.get('fetched'):
            report(status='preparing',message=f"取得 {discovery['fetched']} 份 JD，正在去重、DeepSeek 分析并生成材料")
            pdf=sys.executable
            if subprocess.run([pdf,'-c','import reportlab'],capture_output=True).returncode:
                pdf=str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
            with (folder/'prepare.log').open('w',encoding='utf-8') as log:
                prep=subprocess.run([sys.executable,'-X','utf8',str(root/'scripts/prepare_batch.py'),'--run-dir',str(folder),
                    '--input',str(output),'--pdf-python',pdf],cwd=root,stdout=log,stderr=log,timeout=600)
            path=folder/'summary.json'
            if not path.exists():raise RuntimeError('材料准备中断，请查看 prepare.log；原始 JD 已保留')
            preparation=json.loads(path.read_text(encoding='utf-8'))
            from manual_workbench import Workbench
            Workbench(root,sorted((root/'tmp').glob('**/queue.json')),discover=True).sync_materials()
            if prep.returncode not in (0,2):raise RuntimeError('材料准备异常退出，已有结果保留')
        result=outcome(discovery,preparation)
        records=json.loads(output.read_text(encoding='utf-8')) if output.exists() else []
        reasons=list(dict.fromkeys(str(r.get('error','')) for r in records if r.get('status') not in ('ok','fetched','success')))
        result['block_reasons']=reasons
        def read_rows(name):
            path=folder/name
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        screened=read_rows('screened-out.json');queue=read_rows('queue.json')
        result['screening']={'excluded':screened,'review':[{'job_key':r.get('job_key',''),'title':r.get('title',''),'reason':r.get('reason','')} for r in queue if r.get('status')=='needs_qualification_review'],
            'not_recommended':[{'title':r.get('title',''),'reason':r.get('reason') or r.get('analysis',{}).get('fit_reason') or str(r.get('analysis',{}).get('confirmed_conflicts') or r.get('analysis',{}).get('fit_score',''))} for r in queue if r.get('status')=='not_recommended']}
        if any(x in ('login_required','security_verification') for x in reasons):
            result['message']+='。需要登录或安全验证：请在 Job Search 的对应网站手动完成登录或验证后再试'
        report(**result)
    except subprocess.TimeoutExpired:report(status='error',message='达到本轮时间上限，任务已停止；已有结果保留')
    except Exception as exc:report(status='error',message=str(exc))
    finally:lock.close()

if __name__=='__main__':run(Path(sys.argv[1]))
