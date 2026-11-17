"""Authenticated localhost bridge to the user-installed Job Search extension."""
import csv,json,re,secrets,time
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from discovery_manager import normalize_options,ACTIVE
from search_rotation import load_queries,ordered_queries,linkedin_search_url,record_attempt,STATE
from job_identity import job_key, official_key

def validate_records(rows,limit):
    if not isinstance(rows,list) or len(rows)>limit+8:raise ValueError('岗位结果数量无效')
    clean=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('岗位格式无效')
        url=row.get('sourceUrl','');p=urlsplit(url)
        if p.scheme!='https' or p.hostname not in ('www.linkedin.com','smu.joinhandshake.com') or p.username or p.password:raise ValueError('岗位来源不允许')
        if row.get('status')=='fetched':
            if not re.search(r'/(?:jobs/view|(?:stu/)?jobs|job-search)/\d+',p.path):raise ValueError('需要具体岗位链接')
            if not isinstance(row.get('text'),str) or not 200<=len(row['text'])<=80000:raise ValueError('JD 不完整或过长')
            if not row.get('title') or not row.get('company'):raise ValueError('缺少岗位或公司名称')
        elif row.get('status') not in ('needs_human','search_empty'):raise ValueError('岗位状态无效')
        key=job_key(url)
        if key in seen:continue
        seen.add(key)
        allowed={k:str(row.get(k,''))[:80000 if k=='text' else 1000] for k in ('sourceUrl','title','company','location','text','status','error','search_keyword')}
        entry=row.get('application_url','')
        if entry:
            e=urlsplit(entry)
            # Any recognized employer ATS posting (also used for official-ID dedup).
            if e.scheme=='https' and not e.username and not e.password and official_key(entry):allowed['application_url']=entry[:2000]
        allowed['_source']='LinkedIn' if p.hostname=='www.linkedin.com' else 'Handshake'
        allowed['discovery_mode']='jobsearch_extension';clean.append(allowed)
    if sum(x['status']=='fetched' for x in clean)>limit:raise ValueError('超过本轮 JD 上限')
    return clean

class JobSearchBridge:
    def __init__(self,workbench,manager):
        self.workbench=workbench;self.manager=manager;self.client=None;self.seen=0;self.task=None;self.claimed=False;self.since=0;self.notice=''
    def status(self):
        if self.task and time.monotonic()-self.since>420:
            self.task=None;self.notice='浏览器搜岗超时，已停止等待；请检查 Job Search 工作台是否仍开着'
        connected=time.monotonic()-self.seen<20
        state=self.manager.status()
        if self.task and self.task.get('kind')=='check':state={'status':'checking','message':f"正在检查 {len(self.task['urls'])} 个岗位是否还开放"}
        elif self.task:state={'status':'searching','message':'Job Search 浏览器搜岗中；保持该工作台标签打开','search_report':self.report}
        elif self.notice:state={'status':'blocked','message':self.notice}
        return dict(state,bridge_connected=connected)
    def poll(self,client):
        if not isinstance(client,str) or not 1<=len(client)<=100:raise ValueError('连接标识无效')
        if self.task and client!=self.client:return {'task':None}
        self.client=client;self.seen=time.monotonic();self.status()
        task=None
        if self.task and not self.claimed:
            self.claimed=True;task=self.task
        return {'task':task}
    def start(self,data):
        options=normalize_options(data)
        if self.task or self.status().get('status') in ACTIVE:raise ValueError('已有任务运行')
        if not self.status()['bridge_connected']:raise ValueError('请在 Job Search 中加载连接扩展并打开本工作台；不再启动独立未登录浏览器')
        queries=options['keywords'] or ordered_queries(load_queries(),STATE,'bridge')[:3]
        searches=[]
        for q in queries:
            if options['source']!='handshake':searches.append({'source':'LinkedIn','keyword':q,'url':linkedin_search_url(q,options['location'],14,['1','2','3'])})
            if options['source']!='linkedin':searches.append({'source':'Handshake','keyword':q,'url':'https://smu.joinhandshake.com/job-search?'+urlencode(dict(query=q,per_page=25,sort='relevance',page=1))})
        excluded=[job_key(r.get('sourceUrl') or r.get('url','')) for r in self.workbench.records().values() if r.get('status') not in ('analysis_failed','materials_failed','blocked','needs_human')]
        if isinstance(self.workbench.root,Path):
            history=self.workbench.root/'applications/applications.csv'
            if history.exists():
                with history.open(encoding='utf-8-sig',newline='') as f:
                    excluded.extend(job_key(r.get('Job URL','')) for r in csv.DictReader(f) if r.get('Status','').lower().startswith('applied'))
        excluded=list(dict.fromkeys(excluded))
        path=self.workbench.root/'config/batch-search.json' if isinstance(self.workbench.root,Path) else None
        config=json.loads(path.read_text(encoding='utf-8')) if path and path.exists() else {}
        self.task=dict(include_titles=config.get('include_titles',[]),exclude_titles=config.get('exclude_titles',[]),id=secrets.token_urlsafe(20),searches=searches,max_pages=3,limit=options['limit'],excluded=excluded)
        self.report={'queries':[]};self.options=options;self.claimed=False;self.since=time.monotonic();self.notice=''
        return self.status()
    def start_check(self,urls):
        """Ask the extension to open each source page read-only and report open / closed / unknown."""
        urls=[u for u in dict.fromkeys(urls) if isinstance(u,str) and urlsplit(u).hostname in ('www.linkedin.com','smu.joinhandshake.com')][:25]
        if not urls:return None
        if self.task or self.manager.status().get('status') in ACTIVE:raise ValueError('已有任务运行')
        if not self.status()['bridge_connected']:raise ValueError('扩展未连接：请保持 Job Search 里的工作台标签打开')
        self.check_result=None
        self.task=dict(kind='check',id=secrets.token_urlsafe(20),urls=urls);self.claimed=False;self.since=time.monotonic();self.notice=''
        return self.task['id']
    def cancel(self,task_id):
        if self.task and self.task.get('id')==task_id:self.task=None
    def progress(self,data):
        if not self.task or data.get('id')!=self.task['id'] or data.get('client')!=self.client:raise ValueError('过期或不匹配的搜岗进度')
        report=data.get('report',{})
        queries=report.get('queries',[]) if isinstance(report,dict) else None
        if not isinstance(queries,list) or len(queries)>6:raise ValueError('搜索进度无效')
        clean=[]
        for row in queries:
            if not isinstance(row,dict):raise ValueError('搜索进度无效')
            if not any(row.get('source')==s['source'] and row.get('keyword')==s['keyword'] for s in self.task['searches']):raise ValueError('未知搜索项')
            item={k:str(row.get(k,''))[:200] for k in ('source','keyword','state','error')}
            for k in ('cards_seen','duplicates','title_skips','fetched','pages_read'):
                v=row.get(k,0)
                if type(v)!=int or not 0<=v<=1000:raise ValueError('搜索计数无效')
                item[k]=v
            clean.append(item)
        self.report={'queries':clean};return {'ok':True}
    def complete(self,data):
        if not self.task or data.get('id')!=self.task['id'] or data.get('client')!=self.client:raise ValueError('过期或不匹配的搜岗结果')
        if self.task.get('kind')=='check':
            rows=data.get('check_results')
            if not isinstance(rows,list):rows=[]
            asked=set(self.task['urls'])
            self.check_result=dict(id=self.task['id'],error=str(data.get('error',''))[:200],
                results=[{'url':r['url'],'state':r.get('state') if r.get('state') in ('open','closed') else 'unknown'} for r in rows if isinstance(r,dict) and r.get('url') in asked])
            self.task=None;return {'ok':True}
        rows=validate_records(data.get('records'),self.task['limit'])
        if 'report' in data:self.progress(data)
        self.options['search_report']=self.report
        # Clear only after handing results to the preparation worker successfully.
        result=self.manager.start_import(rows,self.options)
        if not self.options['keywords']:
            for attempted in data.get('attempted',[]):
                if attempted in load_queries():record_attempt(load_queries(),STATE,'bridge',attempted)
        self.task=None;self.notice='';return result
