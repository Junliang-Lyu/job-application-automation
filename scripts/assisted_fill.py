"""Conservative Playwright form assistant. Never clicks navigation or submit."""
import argparse
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def supported_url(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and p.hostname in ('jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io','job-boards.greenhouse.io') and not p.username and not p.password and len(p.path.strip('/').split('/'))>=2
    except ValueError:return False


def read_facts(path):
    facts={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if ': ' in line:
            k,v=line.split(': ',1);facts[k.strip()]=v.strip()
    return facts


def norm(label):
    return re.sub(r'\s+',' ',label.lower().replace('*','')).strip().rstrip('?: ')


def answer_for(label,facts):
    label=norm(label)
    name=facts.get('Name','').split()
    if label in ('first name','legal first name'):return name[0] if len(name)==2 else None
    if label in ('last name','legal last name'):return name[1] if len(name)==2 else None
    aliases={'full name':'Name','name':'Name','email':'Primary Application Email','email address':'Primary Application Email',
        'phone':'Phone','phone number':'Phone','mobile phone':'Phone','address':'Address','street address':'Address',
        'current location':'Current Location','location (city)':'Current Location','school':'University','university':'University',
        'degree':'Degree','graduation date':'Exact Graduation Date','expected graduation date':'Exact Graduation Date',
        'linkedin':'LinkedIn','linkedin profile':'LinkedIn','linkedin url':'LinkedIn','github':'GitHub','portfolio':'Portfolio',
        'will you now or in the future require sponsorship':'Future Employer Sponsorship Required',
        'will you now or in the future require visa sponsorship':'Future Employer Sponsorship Required',
        'will you now or in the future require sponsorship for employment visa status':'Future Employer Sponsorship Required',
        'are you legally authorized to work in the united states':'Currently Authorized to Work in the United States',
        'are you authorized to work in the united states':'Currently Authorized to Work in the United States'}
    value=facts.get(aliases.get(label,''))
    if not value:return None
    if label in ('email','email address'):value=value.split(' (')[0]
    match=re.fullmatch(r'\[([^]]+)\]\([^)]+\)',value)
    return match[1] if match else value


SCAN=r'''() => {
 const elements=[...document.querySelectorAll('input,select,textarea')];
 const labelText=(node)=>{if(!node)return '';const c=node.cloneNode(true);c.querySelectorAll('input,select,textarea,button,script,style').forEach(x=>x.remove());return c.textContent.trim();};
 return elements.map((e,index)=>{
   const type=(e.type||e.tagName).toLowerCase();
   const visible=!!(e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
   let label=[...(e.labels||[])].map(labelText).join(' ').trim()||e.getAttribute('aria-label')||'';
   const block=e.closest('fieldset,.field,.application-question,.custom-question,.question');
   let question=labelText(block?.querySelector('legend,.application-label,.label,label'));
   if(!label && type==='file')label=question;
   return {index,type,tag:e.tagName,label,question,name:e.name,value:type==='file'?'':e.value,
     checked:e.checked,disabled:e.disabled,readonly:e.readOnly,visible,required:e.required,
     role:e.getAttribute('role'),autocomplete:e.getAttribute('autocomplete'),
     options:e.tagName==='SELECT'?[...e.options].map(o=>({label:o.text,value:o.value})):[]};
 }).filter(e=>!e.disabled&&!e.readonly&&!['hidden','submit','button','reset'].includes(e.type)&&(e.visible||e.type==='file'));
}'''


def obstruction(page):
    if page.locator('input[type=password]:visible').count():return '检测到登录或密码字段，请手动操作'
    if page.locator('iframe:visible').count():return '检测到嵌入框（可能是验证码或嵌入表单），请人工检查'
    text=page.locator('body').inner_text(timeout=5000).lower()
    if any(s in text for s in ('verify you are human','confirm you are human','请确认你是人类','验证您是真人')):
        return '检测到人机验证，请你完成'
    return ''


def fill_page(page,facts,resume,report,guard=lambda url:True):
    """Bounded synchronous run. Return control at the first unresolved field."""
    done=set()
    for _ in range(60):
        if not guard(page.url):return report('paused','页面跳转到未支持的域名，已停止')
        blocked=obstruction(page)
        if blocked:return report('paused',blocked)
        fields=page.evaluate(SCAN)
        field=next((f for f in fields if (f['index'],f['label'],f['question']) not in done),None)
        if not field:return report('review','可识别字段处理完成；请检查附件和所有内容，自行点击 Submit')
        f=field; ident=(f['index'],f['label'],f['question'])
        control=page.locator('input,select,textarea').nth(f['index'])
        label=f['question'] if f['type']=='radio' else f['label']
        label=label.strip()
        if f['type']=='file':
            if norm(label) not in ('resume','resume/cv','resume / cv','cv','attach resume','upload resume'):
                return report('paused','无法确认上传框用途：'+(label or '无标签上传框'))
            if control.evaluate('(e)=>e.files.length'):
                done.add(ident);report('filling','保留已有附件');continue
            control.set_input_files(str(resume),timeout=8000)
            if control.evaluate('(e)=>e.files.length')!=1:return report('paused','附件未被控件接受，请检查')
            done.add(ident);report('filling','已选择简历：'+resume.name+'；请确认网站上传完成');continue
        if f['type']=='checkbox' or f['tag']=='TEXTAREA':
            return report('paused','请人工填写：'+(label or '未标注的开放题或勾选项'))
        answer=answer_for(label,facts)
        if answer is None:return report('paused','档案无法明确回答：'+(label or '无标签字段'))
        if f['role']=='combobox':return report('paused','自定义下拉框需人工处理：'+label)
        if f['type']=='radio':
            group=[x for x in fields if x['type']=='radio' and x['name']==f['name'] and x['question']==f['question']]
            if not f['name']:return report('paused','无法确定单选组：'+label)
            checked=next((x for x in group if x['checked']),None)
            if checked and norm(checked['label'])!=norm(answer):return report('paused','已有答案与档案不一致：'+label)
            matches=[x for x in group if norm(x['label'])==norm(answer)]
            if len(matches)!=1:return report('paused','找不到唯一匹配选项：'+label)
            target=page.locator('input,select,textarea').nth(matches[0]['index'])
            target.check(timeout=5000)
            if not target.is_checked():return report('paused','单选核对失败：'+label)
            done.update((x['index'],x['label'],x['question']) for x in group)
        elif f['tag']=='SELECT':
            options=[o for o in f['options'] if norm(o['label'])==norm(answer)]
            if len(options)!=1:return report('paused','下拉选项不明确：'+label)
            if f['value'] and f['value']!=options[0]['value']:return report('paused','保留已有选择，请核对：'+label)
            control.select_option(value=options[0]['value'],timeout=5000)
            if control.input_value()!=options[0]['value']:return report('paused','选项核对失败：'+label)
            done.add(ident)
        elif f['type'] in ('text','email','tel','url'):
            if f['value'] and f['value'].strip()!=answer.strip():return report('paused','保留已有内容，请核对：'+label)
            control.fill(answer,timeout=5000)
            if control.input_value()!=answer:return report('paused','填写后核对失败：'+label)
            done.add(ident)
        else:return report('paused','暂不支持的控件：'+label)
        report('filling','已填写并核对：'+label)
    report('paused','已达到本轮字段上限，请人工检查')


def run(request_path):
    request=json.loads(request_path.read_text(encoding='utf-8'))
    state_path=request_path.with_name('status.json')
    state={'job_key':request['job_key'],'history':[]}
    def report(status,message):
        state.update(status=status,message=message)
        state['history']=(state['history']+[message])[-20:]
        tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');tmp.replace(state_path)
    lock=socket.socket()
    try:lock.bind(('127.0.0.1',18766))
    except OSError:
        report('error','已有辅助填写浏览器运行或端口被占用，请先关闭那份浏览器');return
    try:
        from playwright.sync_api import sync_playwright
        if not supported_url(request['url']):raise ValueError('Unsupported application URL')
        resume=Path(request['resume']).resolve()
        if not resume.is_relative_to(ROOT) or not resume.is_file() or resume.suffix.lower()!='.pdf':raise ValueError('Invalid resume PDF')
        with sync_playwright() as pw:
            context=pw.chromium.launch_persistent_context(str(ROOT/'.assisted-fill-profile'),channel='msedge',headless=False)
            page=context.pages[0] if context.pages else context.new_page()
            report('opening','正在独立浏览器中打开申请页')
            try:
                page.goto(request['url'],wait_until='domcontentloaded',timeout=25000)
                page.locator('input,select,textarea').first.wait_for(state='attached',timeout=10000)
                fill_page(page,read_facts(ROOT/'profile.md'),resume,report,supported_url)
            except Exception as e:report('paused','自动填写已停止，请在浏览器检查。'+type(e).__name__)
            # Keep Playwright alive for human review. Never continue filling or inspect submission.
            while context.pages:
                try:context.pages[0].wait_for_timeout(1000)
                except Exception:break
            report('closed','浏览器已关闭；请回工作台记录实际投递结果')
    except Exception as e:report('error','启动失败：'+type(e).__name__+'；请检查 Playwright/Edge 环境')
    finally:lock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    run(Path(parser.parse_args().request))
