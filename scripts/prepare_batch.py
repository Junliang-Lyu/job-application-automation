"""Prepare a bounded, resumable batch. Never controls a browser or submits jobs."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from search_rotation import linkedin_search_url
from qualification import split_requirements, hard_conflicts, looks_like_agency
from job_identity import job_key, employer_key, prepare_records, repost_family, recent_applied_families, record_official_key, applied_official_keys, analyzed_index
from build_apply_queue import load_submitted_urls, load_excluded_urls, detect_platform, pick_resume
import analyze_jds
from card_jds import fetch_cards, hard_exclusion

ROOT = Path(__file__).resolve().parents[1]

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def words_match(text, terms):
    return any(re.search(r'(?<!\w)' + re.escape(term.lower()) + r'(?!\w)', text.lower()) for term in terms)

def location_allowed(location,config):
    if words_match(location,config['locations']):return True
    if config.get('location_scope')!='us':return False
    states='AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split()
    return bool(re.search(r',\s*(?:'+ '|'.join(states)+r')\b',location))

def select_candidates(records, config, excluded=(), applied_families=None, applied_official=None, analyzed=None):
    blocked = {job_key(u) for u in excluded}
    applied_families = applied_families or {}
    applied_official = applied_official or {}
    analyzed_families, analyzed_official = analyzed or ({}, {})
    seen, selected, rejected, eligible = set(), [], [], []
    for r in records:
        key = job_key(r.get('sourceUrl', ''))
        title, location = r.get('title', ''), r.get('location', '')
        reason = None
        if r.get('status') not in ('ok', 'fetched', 'success') or not r.get('text'): reason = 'fetch_failed_or_empty'
        elif not key or key in blocked or key in seen: reason = 'already_applied_excluded_or_duplicate'
        elif record_official_key(r) in applied_official:
            reason = 'same_official_job_as_applied:' + applied_official[record_official_key(r)][0]
        elif repost_family(r.get('company') or employer_key(r), title) in applied_families:
            when = applied_families[repost_family(r.get('company') or employer_key(r), title)][0]
            reason = 'likely_repost_of_applied:' + when
        elif record_official_key(r) in analyzed_official:
            reason = 'already_analyzed:' + analyzed_official[record_official_key(r)][0]
        elif repost_family(r.get('company') or employer_key(r), title) in analyzed_families:
            reason = 'already_analyzed:' + analyzed_families[repost_family(r.get('company') or employer_key(r), title)][0]
        elif hard_exclusion(r.get('text','')): reason = hard_exclusion(r['text'])
        elif not words_match(title, config['include_titles']): reason = 'title_outside_target'
        elif words_match(title, config['exclude_titles']): reason = 'excluded_seniority_or_type'
        elif not location and not config['allow_unknown_location']: reason = 'location_unknown'
        elif location and not location_allowed(location,config): reason = 'location_outside_target'
        if reason:
            rejected.append({'url':r.get('sourceUrl'), 'title':title,'reason':reason})
            continue
        seen.add(key)
        eligible.append(r)
    # Prefer the candidate's locations and explicit early-career titles, retaining stable order.
    eligible.sort(key=lambda r: (not words_match(r.get('location',''), config['preferred_locations']),
                                 not words_match(r.get('title',''), ['new grad','entry','junior','graduate'])))
    companies, sources, families = {}, {}, set()
    for r in prepare_records(eligible):
        company = employer_key(r) or r['job_key']
        source = r.get('_source','Other')
        family = r['job_family']
        if len(selected) >= config['max_candidates']: reason='batch_limit'
        elif family and family in families: reason='related_role_held'
        elif companies.get(company,0) >= config['max_per_company']: reason='company_limit'
        elif sources.get(source,0) >= config['max_per_source']: reason='source_limit'
        else:
            selected.append(r)
            companies[company]=companies.get(company,0)+1
            sources[source]=sources.get(source,0)+1
            if family: families.add(family)
            continue
        rejected.append({'url':r['sourceUrl'],'title':r['title'],'reason':reason})
    return selected,rejected

def cache_key(record, prompt, model):
    data=[job_key(record['sourceUrl']),record.get('title'),record.get('text'),prompt,model]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False).encode()).hexdigest()

def validate_pdf(path):
    if not path.is_file() or not path.read_bytes().startswith(b'%PDF-'):
        raise ValueError(f'PDF missing or invalid: {path}')
    return str(path.resolve())

def prepare_candidates(records, analyze, make_pdf, min_score=70, checkpoint=None):
    results=[]
    for r in records:
        entry={**r,'url':r['sourceUrl'],'job_key':job_key(r['sourceUrl']),'status':'analysis_failed',
               'fetched_at':r.get('fetched_at') or datetime.now(timezone.utc).date().isoformat()}
        try:
            a=analyze(r)
            if not isinstance(a.get('fit_score'),(int,float)) or not isinstance(a.get('worth_applying'),bool):
                raise ValueError('Invalid analysis score/decision types')
            entry['analysis']=a
            entry['status']='not_recommended'
            unknown=a.get('unknown_requirements',[])
            conflicts=a.get('confirmed_conflicts',[])
            if a.get('stem_related') is False and not conflicts:
                conflicts=['与 STEM 学位不直接相关，不符合 STEM OPT 要求']
                a['confirmed_conflicts']=conflicts
            hard=[c for c in hard_conflicts(a) if c not in conflicts]
            if hard:
                conflicts=list(conflicts)+hard;a['confirmed_conflicts']=conflicts
                entry['reason']='；'.join(hard)
            if looks_like_agency(a,r.get('text',''),r.get('company','')):entry['agency']=True
            blockers,notes=split_requirements(unknown)  # only explicit hard limits hold a job back
            review=bool(blockers and not conflicts)
            if (a['worth_applying'] or unknown) and not conflicts and a['fit_score'] >= min_score:
                entry['status']='materials_failed'
                if not isinstance(a.get('cover_letter_body'),str) or not a['cover_letter_body'].strip():
                    raise ValueError('Missing cover letter body')
                entry['cover_letter']=make_pdf(r,a)
                entry.update(status='needs_qualification_review' if review else 'ready_for_review',resume=str((ROOT/pick_resume(a)).resolve()),
                             platform=detect_platform(r['sourceUrl']),role=r['title'],fit_score=a['fit_score'])
                if review:entry['reason']='需要核对：'+'；'.join(blockers)
                if notes:entry['hints']=notes
        except Exception as exc:
            entry['error']=str(exc)
        results.append(entry)
        if checkpoint: checkpoint(results)
    return results

def merge_progress(existing, new):
    by_key={r['job_key']:r for r in existing}
    for r in new:
        key=r['job_key']
        if key not in by_key or by_key[key].get('status') in ('analysis_failed','materials_failed'):
            by_key[key]=r
    return list(by_key.values())

def load_progress(run):
    queue=json.loads((run/'queue.json').read_text(encoding='utf-8')) if (run/'queue.json').exists() else []
    rows={r['job_key']:r for r in queue}
    journal=run/'events.jsonl'
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            if not line.strip(): continue
            # Fail closed on a damaged journal; never silently retry an uncertain submission.
            event=json.loads(line)
            row=rows.get(event['job_key'])
            if row is None: raise ValueError('Journal references a missing job; manual recovery required')
            if row['status']=='applied' and event['status']!='applied': continue
            row.update(event)
    return queue

def discovery_outcome(records):
    records=[r for r in records if r.get('status')!='search_empty']
    failures=sum(r.get('status') not in ('ok','fetched','success') or not r.get('text') for r in records)
    return {'fetch_failures':failures,'usable_records':len(records)-failures,
            'discovery_failed':bool(records) and failures==len(records),
            'discovery_partial':0 < failures < len(records)}

def record_result(run, key, status, reason, evidence=None, confirmation_url=None):
    allowed={'awaiting_authorization','needs_user_video','needs_user_input','in_progress',
             'submission_unknown','applied','not_recommended','blocked'}
    if status not in allowed: raise ValueError('Unsupported result status')
    if status=='applied' and (not evidence or not Path(evidence).is_file() or not confirmation_url):
        raise ValueError('Applied requires saved confirmation evidence and the verified confirmation URL')
    queue=load_progress(run)
    row=next((r for r in queue if r['job_key']==key),None)
    if row is None: raise ValueError('Job not in this batch')
    if row['status']=='applied' and status!='applied': raise ValueError('Cannot downgrade a confirmed application')
    event={'at':datetime.now(timezone.utc).isoformat(),'job_key':key,'status':status,'reason':reason,
           'evidence':str(evidence) if evidence else None,'confirmation_url':confirmation_url}
    row.update(event)
    # Journal first so an interruption between writes still leaves recovery evidence.
    with (run/'events.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(event,ensure_ascii=False)+'\n')
    save(run/'queue.json',queue)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config/batch-search.json'))
    parser.add_argument('--run-dir',required=True)
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--input',help='Reuse an explicit fetched snapshot; no silent fallback')
    inputs.add_argument('--cards',help='Browser-exported job cards; fetch public descriptions without browser credentials')
    parser.add_argument('--discover-only',action='store_true')
    parser.add_argument('--pdf-python',default=sys.executable)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    run=Path(args.run_dir).resolve();run.mkdir(parents=True,exist_ok=True)
    from search_rotation import load_queries, ordered_queries
    queries=ordered_queries(load_queries())
    search_plan=[{'source':'LinkedIn','keyword':q,'location':loc,'url':linkedin_search_url(q,loc,config.get('linkedin_posted_days',14),
                   config.get('linkedin_workplace_types',['1','2','3']))}
                 for q in queries for loc in config.get('linkedin_locations',['United States'])]
    search_plan += [{'source':'Handshake','keyword':q,'location':loc,
                     'url':'https://smu.joinhandshake.com/job-search',
                     'instructions':'Use visible keyword/location filters in the existing Job Search browser; export complete JD and metadata. Do not submit an application merely to discover its destination.'}
                    for q in queries for loc in config.get('linkedin_locations',['United States'])]
    save(run/'browser-search-plan.json',search_plan)
    if not args.input and not args.cards:
        summary={'status':'needs_platform_discovery','sources':['LinkedIn','Handshake'],
                 'instructions':'Codex: export a bounded visible job-card list from the existing logged-in browser and rerun with --cards; or save full JD records and use --input. No discovery has run yet.',
                 'submission_performed_by_script':False}
        save(run/'summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False))
        sys.exit(3)
    old=load_progress(run)
    for item in old:
        item['job_key']=job_key(item.get('sourceUrl') or item['url'])
    excluded=load_submitted_urls(ROOT/'applications/applications.csv')|load_excluded_urls(ROOT/'applications/excluded-jobs.local.json')
    if args.cards:
        rows=json.loads(Path(args.cards).read_text(encoding='utf-8'))
        prior=json.loads((run/'fetched.json').read_text(encoding='utf-8')) if (run/'fetched.json').exists() else []
        records,fetch_stats=fetch_cards(rows,prior=prior,excluded=excluded)
        save(run/'fetch-summary.json',fetch_stats)
    else:
        records=json.loads(Path(args.input).read_text(encoding='utf-8'))
    save(run/'fetched.json',records)
    discovery=discovery_outcome(records)
    applied_families=recent_applied_families(ROOT/'applications/applications.csv',config.get('repost_window_days',60))
    applied_official=applied_official_keys(ROOT/'applications/applications.csv')
    analyzed=analyzed_index(ROOT,run,config.get('repost_window_days',60))  # same role analysed in an earlier batch
    selected,rejected=select_candidates(records,config,excluded,applied_families,applied_official,analyzed)
    save(run/'selected.json',selected);save(run/'screened-out.json',rejected);save(run/'config.json',config)
    save(run/'needs-browser.json',[r for r in records if r.get('status')=='needs_browser'])
    if args.discover_only or discovery['discovery_failed']:
        summary={**discovery,'fetched':len(records),'selected':len(selected),
                 'submission_performed_by_script':False}
        save(run/'summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False))
        if discovery['fetch_failures']: sys.exit(2)
        return
    validate_pdf(ROOT/'resume/main/Junliang_Lyu_Main_Resume_v5_1.pdf')
    probe=subprocess.run([args.pdf_python,'-c','import reportlab'],capture_output=True)
    if probe.returncode: raise RuntimeError('PDF runtime lacks reportlab; pass --pdf-python with the existing bundled runtime')
    llm=analyze_jds.get_analysis_llm_config()
    if llm['provider']!='deepseek' or not llm['api_key']: raise ValueError('Configure the authorized DeepSeek provider')
    cache=ROOT/'tmp/analysis-cache';cache.mkdir(parents=True,exist_ok=True)
    def analyze(r):
        key=cache_key(r,analyze_jds.SYSTEM_PROMPT+analyze_jds.USER_TEMPLATE,llm['model'])
        path=cache/(key+'.json')
        if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
        a=analyze_jds.analyze_one_deepseek(llm['api_key'],llm['base_url'],r,llm['model'])
        if isinstance(a,dict) and isinstance(a.get('fit_score'),(int,float)) and isinstance(a.get('worth_applying'),bool) and a.get('cover_letter_body'):
            save(path,a)
        return a
    def make_pdf(r,a):
        jobdir=run/'materials'/hashlib.sha256(job_key(r['sourceUrl']).encode()).hexdigest()[:16]
        jobdir.mkdir(parents=True,exist_ok=True)
        body=jobdir/'body.txt';body.write_text(a['cover_letter_body'],encoding='utf-8')
        company=r.get('company') or employer_key(r)
        if not company: raise ValueError('Missing verified company name')
        subprocess.run([args.pdf_python,str(ROOT/'scripts/gen_cover_letter.py'),'--company',company,'--role',r['title'],
                        '--body-file',str(body),'--out-dir',str(jobdir)],check=True,capture_output=True)
        pdfs=list(jobdir.glob('*.pdf'))
        if len(pdfs)!=1: raise ValueError('Expected exactly one job-specific PDF')
        return validate_pdf(pdfs[0])
    prior={r['job_key'] for r in old if r.get('status') not in ('analysis_failed','materials_failed')}
    pending=[r for r in selected if r['job_key'] not in prior]
    results=prepare_candidates(pending,analyze,make_pdf,config['min_score'],lambda rows:save(run/'queue.json',merge_progress(old,rows)))
    queue=merge_progress(old,results);save(run/'queue.json',queue)
    summary={**discovery,'updated_at':datetime.now(timezone.utc).isoformat(),'fetched':len(records),'selected':len(selected),
             'statuses':{s:sum(r['status']==s for r in queue) for s in sorted({r['status'] for r in queue})},
             'submission_performed_by_script':False}
    save(run/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))
    if discovery['fetch_failures'] or any(r['status'] in ('analysis_failed','materials_failed') for r in queue): sys.exit(2)

if __name__=='__main__':main()
