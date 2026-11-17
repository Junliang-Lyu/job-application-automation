"""Readable answer cards and deterministic local material bundles; no LLM calls."""
import hashlib
import re
import shutil
from pathlib import Path

GROUPS = {
    '联系信息': [('Name','姓名'),('Primary Application Email','申请邮箱'),('Phone','电话'),
        ('Current Location','城市'),('Address','地址'),('LinkedIn','LinkedIn'),('GitHub','GitHub'),('Portfolio','作品集')],
    '教育背景': [('University','研究生学校'),('School','学院'),('Degree','研究生学位'),
        ('Graduate Start','入学时间'),('Exact Graduation Date','预计毕业日期'),('GPA','研究生 GPA'),
        ('Undergraduate University','本科学校'),('Undergraduate Degree','本科学位'),
        ('Undergraduate Major','本科专业'),('Undergraduate Start','本科入学'),('Undergraduate Graduation','本科毕业')],
    '工作经历': [('Employer','雇主'),('Title','职位'),('Employment Dates','任职时间'),('Location','工作地点'),
        ('Prior internship count','实习段数'),('PostgreSQL work experience','PostgreSQL 工作经验'),
        ('Python','Python 使用'),('SQL','SQL 使用')],
    '工作授权与到岗': [('Visa Status','签证身份'),('Future Employer Sponsorship Required','未来需要雇主赞助'),
        ('U.S. Citizen / Permanent Resident','美国公民或绿卡'),('Current/Post-Graduation Work Authorization','OPT 情况'),
        ('Earliest Start Date','最早到岗'),('Relocation','愿意搬迁'),('Travel','愿意出差')],
    '自愿身份信息': [('Gender (if requested)','Gender'),('Race/Ethnicity (if requested)','Race'),('Detailed Race/Ethnicity (if requested)','Race 细分'),('Veteran Status','Veteran Status'),('Disability self-identification (ADA question)','Disability (ADA)')],
    '工作授权常见题': [('Authorized to work for any employer in the U.S. (user-confirmed answer)','Authorized to work in U.S.'),('Authorized to work for any employer in the United States or Canada (user-confirmed answer)','Authorized to work in U.S. or Canada')]
}


def answer_cards(root):
    path = Path(root)/'profile.md'
    if not path.exists(): return []
    lines = path.read_text(encoding='utf-8').splitlines()
    facts = {}
    for i,line in enumerate(lines):
        if ': ' in line:
            key,value = line.split(': ',1); facts[key.strip()] = value.strip()
        elif line.endswith(':'):
            value = next((x.strip() for x in lines[i+1:] if x.strip()),'')
            facts[line[:-1].strip()] = value
    groups = []
    for name, fields in GROUPS.items():
        cards=[]
        for key,label in fields:
            if key not in facts: continue
            raw=facts[key]; value=raw; warning=''
            match=re.fullmatch(r'\[([^]]+)\]\([^)]+\)',value)
            if match: value=match[1]
            if key=='Primary Application Email': value=value.split(' (')[0]
            if key=='Prior internship count':
                value=value.split(' (')[0];warning='仅确认段数；公司、日期和职责尚未提供。'
            if key=='PostgreSQL work experience': value=value.split(' (')[0]
            if key in ('Current/Post-Graduation Work Authorization','Earliest Start Date'):
                warning='OPT 计划不等于已有生效许可；按岗位题目和实际许可填写。'
            if key=='Future Employer Sponsorship Required':warning='是否接受无 H1B 赞助岗位，与未来是否需要赞助是不同问题。'
            cards.append(dict(label=label,value=value,warning=warning,source='profile.md'))
        groups.append(dict(title=name,cards=cards))
    return groups


def project_cards(root):
    path=Path(root)/'job-application-answer-bank.md'
    if not path.exists():return []
    text=path.read_text(encoding='utf-8')
    result=[]
    for section in re.split(r'(?m)^## ',text)[1:]:
        title,_,body=section.partition('\n')
        if not re.match(r'\d+\.',title): continue
        # Retain source wording; these are reference facts, not invented interview answers.
        body=body.split('\n# ')[0].replace('------------------------------------------------------------------------','').strip()
        body=re.sub(r'\*\*([^*]+)\*\*',r'\1',body)
        result.append(dict(title=title,body=body))
    return result


UPLOAD_RESUME_NAME = 'Junliang_Lyu_Resume.pdf'  # employers never see which version it is


UPLOAD_COVER_NAME = 'Junliang_Lyu_Cover_Letter.pdf'


def upload_root():
    """Short ASCII folder for the file dialog. Typing the project path (Chinese folder names, 150+ characters)
    into a Windows file dialog is where Codex uploads failed."""
    import os
    return Path(os.environ.get('JOB_UPLOAD_DIR') or Path.home() / 'JobUploads')


def upload_folder(row):
    return upload_root() / hashlib.sha256(row['job_key'].encode()).hexdigest()[:8]


def upload_name(kind, source):
    return UPLOAD_RESUME_NAME if kind == 'resume' and source.suffix.lower() == '.pdf' else source.name


def bundle_folder(root, row):
    root=Path(root).resolve()
    key=row['job_key']
    label=re.sub(r'[^\w .-]+','_',f"{row.get('company','Company')}_{row.get('title') or row.get('role','Job')}").strip(' .')[:85]
    folder=(root/'applications/materials'/f"job_{label}_{hashlib.sha256(key.encode()).hexdigest()[:10]}").resolve()
    if not folder.is_relative_to(root/'applications/materials'):raise ValueError('Invalid bundle path')
    return folder


def bundle(root, row, resume, letter):
    folder=bundle_folder(root,row)
    folder.mkdir(parents=True,exist_ok=True)
    files={}
    for kind,source in [('resume',resume),('cover_letter',letter)]:
        if source:
            target=folder/upload_name(kind,source)
            if not target.exists() or target.read_bytes()!=source.read_bytes():shutil.copyfile(source,target)
            files[kind]=str(target)
    try:
        short=upload_folder(row);short.mkdir(parents=True,exist_ok=True)
        for kind,name in (('resume',UPLOAD_RESUME_NAME),('cover_letter',UPLOAD_COVER_NAME)):
            source=Path(files[kind]) if kind in files else None
            if source and source.suffix.lower()=='.pdf':
                target=short/name
                if not target.exists() or target.read_bytes()!=source.read_bytes():shutil.copyfile(source,target)
                files['upload_'+kind]=str(target)
    except OSError:
        pass  # the long-path copies above still work for manual uploads
    body=(row.get('analysis') or {}).get('cover_letter_body','')
    if body:
        target=folder/'Cover_Letter.txt'
        if not target.exists() or target.read_text(encoding='utf-8')!=body:target.write_text(body,encoding='utf-8')
        files['letter_text']=str(target)
    jd=row.get('text','')
    if jd:
        target=folder/'Job_Description.txt'
        if not target.exists() or target.read_text(encoding='utf-8')!=jd:target.write_text(jd,encoding='utf-8')
    return dict(folder=str(folder),files=files)
