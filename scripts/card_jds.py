"""Read public JD pages from browser-exported cards; never uses browser credentials."""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from job_identity import job_key


class DescriptionParser(HTMLParser):
    VOID={'br','img','input','meta','link','hr','source','wbr','area','base','embed','param','track','col'}
    def __init__(self):
        super().__init__(); self.depth=0; self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in self.VOID:
            if self.depth and tag=='br': self.parts.append('\n')
            return
        if self.depth:
            self.depth+=1
            if tag in ('script','style','noscript'): self.skip+=1
        elif 'description__text' in (dict(attrs).get('class') or '').split(): self.depth=1
    def handle_endtag(self,tag):
        if tag in self.VOID or not self.depth: return
        if tag in ('script','style','noscript') and self.skip: self.skip-=1
        self.depth-=1
        if not self.skip: self.parts.append('\n')
    def handle_data(self,text):
        if self.depth and not self.skip: self.parts.append(text)


def description_text(html):
    parser=DescriptionParser(); parser.feed(html)
    return '\n'.join(s.strip() for s in ''.join(parser.parts).splitlines()
                     if s.strip() and s.strip() not in ('Show more','Show less'))


def fetch_one(row):
    result={**row,'status':'needs_browser'}
    url=row['sourceUrl']; host=(urlsplit(url).hostname or '').lower()
    if host not in ('linkedin.com','www.linkedin.com') or '/jobs/view/' not in urlsplit(url).path:
        return {**result,'error':'public_detail_adapter_unavailable; preserve for browser reading'}
    started=time.monotonic()
    try:
        request=Request(url,headers={'User-Agent':'JobSearchJDPreprocessor/1.0'})
        with urlopen(request,timeout=12) as response:
            final=response.url; raw=response.read(2_000_001)
        if any(s in urlsplit(final).path for s in ('/authwall','/login','/checkpoint')):
            return {**result,'error':'login_required'}
        if len(raw)>2_000_000: return {**result,'error':'response_too_large'}
        text=description_text(raw.decode('utf-8',errors='replace'))
        if len(text)<200: return {**result,'error':'public_description_unavailable'}
        return {**row,'status':'fetched','text':text,'contentHash':hashlib.sha256(text.encode()).hexdigest(),
                'fetchedAt':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-started,2),
                'official_requirements_verified':False,'_source':row.get('_source','LinkedIn')}
    except Exception as exc:
        return {**result,'error':str(exc)[:200]}


def fetch_cards(rows,prior=(),excluded=(),fetch=fetch_one):
    """Reuse only this run's successful checkpoint; other failures stay retryable."""
    blocked={job_key(u) for u in excluded}
    cached={job_key(r['sourceUrl']):r for r in prior if r.get('status')=='fetched' and r.get('text')}
    seen=set(); selected=[]; stats={'exported':len(rows),'requested':0,'reused':0,'duplicates_or_excluded':0}
    for row in rows:
        key=job_key(row.get('sourceUrl',''))
        if not key or key in blocked or key in seen:
            stats['duplicates_or_excluded']+=1;continue
        seen.add(key);selected.append(row)
    def one(row):
        key=job_key(row['sourceUrl'])
        if key in cached:return cached[key]
        try:return fetch(row)
        except Exception as exc:return {**row,'status':'needs_browser','error':str(exc)[:200]}
    stats['reused']=sum(job_key(r['sourceUrl']) in cached for r in selected)
    stats['requested']=len(selected)-stats['reused']
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(one,selected))
    return results,stats


def hard_exclusion(text):
    """Conservative citizenship exclusions; no-sponsorship alone does not rule out OPT."""
    text=re.sub(r'\s+',' ',text).lower()
    if re.search(r'(?<!no )(?<!not )(?:u\.?s\.?|united states) citizenship (?:is )?required',text):return 'citizenship_required'
    if re.search(r'sole united states citizenship.{0,100}required',text):return 'citizenship_required'
    return None
