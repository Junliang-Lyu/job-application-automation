// Serialized into the page by chrome.scripting. Reads DOM only; never clicks forms.
function readJobPage(){
 const text=(selector)=>document.querySelector(selector)?.innerText?.trim()||'';
 const body=document.body?.innerText||'';
 if(/\/authwall|\/login|\/signin/.test(location.pathname)||document.querySelector('input[type="password"]'))return {blocked:'login_required'};
 if(/verify you are human|checking your browser|verify your identity|人机验证|验证您是人类/i.test(body))return {blocked:'security_verification'};
 let collapsed=false;
 const linkedin=location.hostname==='www.linkedin.com';
 const emptyResults=/no matching jobs|no jobs found|no results found|0 jobs found|没有找到.*职位|未找到.*职位/i.test(body);
 const loading=!!document.querySelector('[aria-busy="true"],[role="progressbar"]');
 const cards=[];
 for(const a of document.querySelectorAll(linkedin?'a[href*="/jobs/view/"]':'[data-hook^="job-result-card"] a[href*="/job-search/"],a[data-hook="job-title"]')){
   const u=new URL(a.href);const match=u.pathname.match(linkedin?/\/jobs\/view\/(?:[^/]*-)?(\d+)/:/\/(?:job-search|(?:stu\/)?jobs)\/(\d+)/);
   if(!match||u.hostname!==location.hostname)continue;
   const url=linkedin?'https://www.linkedin.com/jobs/view/'+match[1]+'/':u.origin+u.pathname;
   if(!cards.some(c=>c.url===url))cards.push({url,title:(a.innerText||a.getAttribute('aria-label')||'').trim()});
 }
 if(linkedin){
   for(const card of document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]')){
     const id=card.getAttribute('componentkey').match(/^job-card-component-ref-(\d+)$/)?.[1];
     const dismiss=card.querySelector('button[aria-label^="Dismiss "]');
     if(!id||!dismiss)continue;
     const url='https://www.linkedin.com/jobs/view/'+id+'/';
     const title=dismiss.getAttribute('aria-label').replace(/^Dismiss /,'').replace(/ job$/,'').trim();
     if(!cards.some(c=>c.url===url))cards.push({url,title});
   }
 }
 let title=text(linkedin?'h1,.job-details-jobs-unified-top-card__job-title':'h1,[data-hook="job-title"]');
 let company=text(linkedin?'.job-details-jobs-unified-top-card__company-name,.topcard__org-name-link':'.employer-name,[data-hook="employer-name"]');
 let locationText=text(linkedin?'.job-details-jobs-unified-top-card__tertiary-description-container,.topcard__flavor--bullet':'[data-hook="job-location"],.job-location');
 let jd=text(linkedin?'.jobs-description__content,.jobs-description-content__text,.show-more-less-html__markup':'.job-description,[data-hook="job-description"]');
 if(!linkedin){
   const jobLink=[...document.querySelectorAll('a[href*="/jobs/"]')].find(e=>e.querySelector('h1'));
   if(jobLink){
     title=jobLink.querySelector('h1').innerText.trim();
     const employer=jobLink.parentElement.querySelector('a[href^="/e/"][aria-label]');
     if(employer)company=employer.getAttribute('aria-label').trim();
   }
   const section=name=>{
     const h=[...document.querySelectorAll('h3')].find(e=>e.innerText.trim()===name);
     return h?.parentElement?.parentElement?.innerText?.trim()||'';
   };
   const description=section('Job description'),glance=section('At a glance');
   const dh=[...document.querySelectorAll('h3')].find(e=>e.innerText.trim()==='Job description');
   collapsed=!!dh?.parentElement?.parentElement?.querySelector('button[aria-label^="Show more"]');
   if(description.length>=200)jd=description+'\n\n'+glance;
   const line=glance.split('\n').find(x=>/based in|Remote|Hybrid|Onsite/.test(x));
   if(line)locationText=line.replace(/^.*based in\s*/, '').trim();
 }
 if(linkedin){
   // LinkedIn's new logged-in layout uses generated classes; use visible semantics.
   const companyNode=document.querySelector('[aria-label^="Company, "]');
   if(!company && companyNode)company=companyNode.getAttribute('aria-label').replace(/^Company,\s*/, '').replace(/\.$/,'').trim();
   const about=[...document.querySelectorAll('h2')].find(e=>['About the job','关于职位','职位简介'].includes(e.innerText.trim()));
   if(!jd && about){
     let region=about.parentElement;
     for(let i=0;i<3 && region;i++,region=region.parentElement){
       const headings=[...region.querySelectorAll('h2')];
       if(headings.length===1 && region.innerText.length>=200){jd=region.innerText.replace(/^(About the job|关于职位|职位简介)\s*/, '').trim();break}
     }
   }
   const pageTitle=document.title.split(' | ');
   if(!title && pageTitle.length>=3 && pageTitle.at(-1)==='LinkedIn')title=pageTitle[0].trim();
   if(!company && pageTitle.length>=3 && pageTitle.at(-1)==='LinkedIn')company=pageTitle[1].trim();
   if(!locationText && companyNode){
     let top=companyNode;
     for(let i=0;i<6 && top;i++,top=top.parentElement){
       const candidate=[...top.querySelectorAll('p')].find(e=>/Reposted|ago|applicants|小时前|申请者/.test(e.innerText)&&e.innerText.includes('·'));
       if(candidate){locationText=candidate.innerText.split('·')[0].trim();break}
     }
   }
 }
 const applicationLinks=[...document.querySelectorAll('a[href]')].map(a=>{
   try{
     let u=new URL(a.href);
     // Only unwrap labelled application links on the supported source domains.
     if(['www.linkedin.com','smu.joinhandshake.com'].includes(u.hostname)&&/apply|continue|申请|投递|继续/i.test((a.innerText||'')+' '+(a.getAttribute('aria-label')||''))){
       const target=u.searchParams.get('url')||u.searchParams.get('redirectUrl')||u.searchParams.get('redirect');
       if(target)u=new URL(target);
     }
     return u;
   }catch{return null}
 }).filter(u=>u&&u.protocol==='https:'&&!u.username&&!u.password&&(['jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io','job-boards.greenhouse.io','jobs.ashbyhq.com','apply.workable.com'].includes(u.hostname)||/(?:^|\.)(?:greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|myworkdayjobs\.com|icims\.com|smartrecruiters\.com|recruitee\.com|applytojob\.com|applicantpro\.com|jobs\.gem\.com|paylocity\.com|jobvite\.com|bamboohr\.com|jibeapply\.com)$/.test(u.hostname))).map(u=>u.href);
 // Closed-posting notice, read only near the job title so sidebar "similar jobs" text is ignored.
 const titleAt=title?body.indexOf(title):-1;
 const topRegion=titleAt>=0?body.slice(titleAt,titleAt+1500):body.slice(0,4000);
 const closed=/no longer accepting applications|not (?:currently )?accepting applications|this job (?:is no longer available|has expired|is closed)|(?:job|posting) (?:has )?expired|applications? (?:are |is )?(?:now )?closed|职位已关闭|不再接受申请/i.test(topRegion);
 return {closed,applicationLinks:[...new Set(applicationLinks)],searchKeyword:document.querySelector('input[placeholder="Describe a job you want"]')?.value||'',emptyResults,loading,cards:cards.slice(0,30),title,company,location:locationText,text:jd,collapsed,diagnostics:{title:!!title,company:!!company,jdLength:jd.length,location:!!locationText,
   readyState:document.readyState,visibility:document.visibilityState,bodyChars:body.length,
   aboutHeading:[...document.querySelectorAll('h2,h3')].some(e=>/About the job|Job description|关于职位|职位简介/.test(e.innerText||''))}};
}
// Fallback for LinkedIn pages opened in a background tab, where the job description is never rendered:
// read the same description from LinkedIn's public job-posting page for this job ID (read-only GET, same origin).
async function fetchLinkedInGuestJD(){
 const id=(location.pathname.match(/\/jobs\/view\/(\d+)/)||[])[1];
 if(!id)return {status:'no_job_id',text:''};
 try{
  const response=await fetch('/jobs-guest/jobs/api/jobPosting/'+id,{credentials:'include'});
  if(!response.ok)return {status:'http_'+response.status,text:''};
  const doc=new DOMParser().parseFromString(await response.text(),'text/html');
  const node=doc.querySelector('.show-more-less-html__markup')||doc.querySelector('.description__text');
  const text=(node?.textContent||'').replace(/[ \t]+/g,' ').replace(/\s*\n\s*/g,'\n').trim();
  return {status:text.length>=200?'ok':'no_description',text};
 }catch(e){return {status:'error:'+String(e.message||e).slice(0,80),text:''}}
}
if(typeof module!=='undefined')module.exports={readJobPage,fetchLinkedInGuestJD};

function expandJobDescription(){
 const heading=[...document.querySelectorAll('h2,h3')].find(e=>['About the job','Job description','关于职位','职位简介'].includes(e.innerText.trim()));
 if(!heading)return false;
 const region=heading.parentElement?.parentElement;
 const button=region?.querySelector('button[aria-label^="Show more"]');
 if(button && button.getAttribute('type')==='button'){button.click();return true}
 return false;
}
