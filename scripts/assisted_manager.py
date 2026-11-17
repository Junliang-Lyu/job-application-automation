"""Launch one user-requested assistant; no browser control in the web server."""
import json
import socket
import subprocess
import sys
from pathlib import Path
from assisted_fill import supported_url


class AssistedManager:
    def __init__(self, workbench):
        self.workbench=workbench
        self.process=None
        self.directory=workbench.root/'tmp/assisted-fill'

    def status(self):
        path=self.directory/'status.json'
        status=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'idle','message':'尚未启动'}
        if self.process and self.process.poll() is not None and status.get('status') in ('starting','opening','filling','paused','review'):
            status.update(status='closed',message='辅助填写进程已结束，请检查浏览器并记录结果')
        return status

    def start(self,key,application_url=None):
        if self.process and self.process.poll() is None:raise ValueError('已有申请浏览器运行；请处理并关闭后再开始下一份')
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',18766))
            except OSError:raise ValueError('已有辅助浏览器运行或端口被占用，请先关闭后再试')
        row=self.workbench.records().get(key)
        if not row:raise ValueError('岗位不存在')
        if row.get('status') in ('applied','submission_unknown'):raise ValueError('已投或结果待确认，不能重新开始填写')
        url=(application_url or '').strip() or row.get('application_url') or row.get('official_url') or row.get('sourceUrl') or row.get('url','')
        if not supported_url(url):raise ValueError('需要已核实的 Greenhouse 或 Lever 官方申请链接；此来源链接暂不支持自动填写')
        resume=self.workbench.resume_file(row)
        if not resume or resume.suffix.lower()!='.pdf':raise ValueError('缺少有效的岗位简历 PDF')
        self.directory.mkdir(parents=True,exist_ok=True)
        request=self.directory/'request.json'
        request.write_text(json.dumps({'job_key':key,'url':url,'resume':str(resume)},ensure_ascii=False),encoding='utf-8')
        (self.directory/'status.json').write_text(json.dumps({'job_key':key,'status':'starting','message':'准备启动独立浏览器'}),encoding='utf-8')
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('assisted_fill.py')),'--request',str(request)],
            cwd=str(self.workbench.root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return self.status()
