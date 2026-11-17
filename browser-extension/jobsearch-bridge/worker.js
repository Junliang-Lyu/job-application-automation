importScripts('extract.js');
let busy=false;
function allowed(url){try{const u=new URL(url);return u.protocol==='https:'&&['www.linkedin.com','smu.joinhandshake.com'].includes(u.hostname)&&!u.username&&!u.password}catch{return false}}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
// Wraps one page read and records how it went, so a failed read can be diagnosed later
// (how long we waited, tab load state, where the tab ended up). Behaviour is unchanged.
async function read(tabId,url,mode='jd'){
 const trace={attempts:0,tabStatus:'',finalPath:'',started:Date.now()};
 let result;
 try{result=await readOnce(tabId,url,mode,trace)}
 finally{if(trace.restoreTabId&&trace.restoreTabId!==tabId)try{await chrome.tabs.update(trace.restoreTabId,{active:true})}catch{}}
 const jdSource=trace.jdSource||((result.text||'').length>=200?(trace.foregroundTried?'foreground':'page'):'none');
 return {...result,diagnostics:{...(result.diagnostics||{}),attempts:trace.attempts,waited_ms:Date.now()-trace.started,
   tab_status:trace.tabStatus,final_path:trace.finalPath,loading:!!result.loading,collapsed:!!result.collapsed,blocked:result.blocked||'',
   jd_source:jdSource,guest_status:trace.guestStatus||'',foreground_tried:!!trace.foregroundTried}};
}
async function readOnce(tabId,url,mode,trace){
 if(!allowed(url))throw Error('unsupported_url');
 await chrome.tabs.update(tabId,{url});
 let result={};
 for(let i=0;i<12;i++){
   await pause(1000);const tab=await chrome.tabs.get(tabId);
   trace.attempts=i+1;trace.tabStatus=tab.status||'';try{const u=new URL(tab.url||'');trace.finalPath=u.hostname+u.pathname}catch{}
   if(!allowed(tab.url||'')){if(tab.status==='complete')return {blocked:'login_or_redirect'};continue}
   if(tab.status!=='complete')continue;
   const readings=await chrome.scripting.executeScript({target:{tabId},func:readJobPage});result=readings[0]?.result||{};
   if(trace.guestText&&(result.text||'').length<200)result={...result,text:trace.guestText,loading:false};
   if(mode==='entry' && (result.blocked||result.applicationLinks?.length||(i>=2&&!result.loading)))return result;
   if(mode==='check' && (result.blocked||result.closed||(i>=2&&!result.loading&&result.title)))return result;
   if(result.collapsed){const expansion=await chrome.scripting.executeScript({target:{tabId},func:expandJobDescription});if(!expansion[0]?.result)return {...result,blocked:'jd_expansion_failed'};continue}
   if(new URL(url).hostname==='smu.joinhandshake.com'&&new URL(url).searchParams.has('query')&&result.searchKeyword!==new URL(url).searchParams.get('query'))continue;
   const detailPage=/\/(?:jobs\/view|(?:stu\/)?jobs|job-search)\/\d+/.test(new URL(url).pathname);
   // LinkedIn only renders the description in a visible tab. Fallbacks, in order:
   // 1) its public job-posting page for the same ID; 2) briefly bring this tab to the front, then restore the user's tab.
   const linkedInDetail=mode==='jd'&&detailPage&&new URL(url).hostname==='www.linkedin.com'&&!result.blocked&&result.title&&(result.text||'').length<200;
   if(linkedInDetail&&i>=3&&!trace.guestTried){
     trace.guestTried=true;
     const guest=(await chrome.scripting.executeScript({target:{tabId},func:fetchLinkedInGuestJD}))[0]?.result||{};
     trace.guestStatus=guest.status||'no_result';
     if((guest.text||'').length>=200){trace.guestText=guest.text;result={...result,text:guest.text,loading:false};trace.jdSource='guest_api'}
   }
   if(linkedInDetail&&!trace.jdSource&&i>=6&&!trace.foregroundTried){
     trace.foregroundTried=true;
     try{const [previous]=await chrome.tabs.query({active:true,windowId:tab.windowId});trace.restoreTabId=previous?.id;await chrome.tabs.update(tabId,{active:true})}catch{}
     continue;
   }
   if(result.blocked||(detailPage?(result.text?.length>=200&&result.title&&result.company&&result.location):result.cards?.length||result.emptyResults))return result;
 }
 return result;
}
async function discover(task,notify=()=>{}){
 if(busy)throw Error('browser_busy');
 if(!task||!Array.isArray(task.searches)||task.searches.length>6||!Number.isInteger(task.limit)||task.limit<1||task.limit>12)throw Error('invalid_task');
 busy=true;let tabId;const report={queries:[]};const records=[],attempted=[],seen=new Set(task.excluded||[]);const deadline=Date.now()+240000;
 const target=title=>{const t=title.toLowerCase();return !(task.exclude_titles||[]).some(x=>new RegExp('\\b'+x+'\\b').test(t))&&(!(task.include_titles||[]).length||(task.include_titles||[]).some(x=>t.includes(x)))};
 const fail=(url,error)=>records.push({sourceUrl:url,status:'needs_human',error});
 try{
  // Only this newly-created tab is navigated or closed. Existing user tabs are untouched.
  tabId=(await chrome.tabs.create({url:'about:blank',active:false})).id;
  const blockedSources=new Set();
  for(const search of task.searches){
   if(records.filter(x=>x.status==='needs_human').length>=6||records.filter(x=>x.status==='fetched').length>=task.limit||Date.now()>deadline)break;
   if(blockedSources.has(search.source))continue;
   attempted.push(search.keyword);
   const metrics={source:search.source,keyword:search.keyword,state:'searching',cards_seen:0,duplicates:0,title_skips:0,fetched:0,pages_read:0};report.queries.push(metrics);notify(report);
   let kept=0;const pageSignatures=new Set();
   const quota=Math.max(2,Math.ceil(task.limit/task.searches.length));
   for(let page=1;page<=Math.min(task.max_pages||3,3);page++){
   if(kept>=quota||records.filter(x=>x.status==='fetched').length>=task.limit||Date.now()>deadline||records.filter(x=>x.status==='needs_human').length>=6)break;
   const pageUrl=new URL(search.url);
   if(search.source==='Handshake')pageUrl.searchParams.set('page',String(page));
   else pageUrl.searchParams.set('start',String((page-1)*25));
   const listing=await read(tabId,pageUrl.href);metrics.pages_read++;
   const signature=(listing.cards||[]).map(c=>c.url).join('|');
   if(signature&&pageSignatures.has(signature)){metrics.error='pagination_not_applied';notify(report);break;}
   if(signature)pageSignatures.add(signature);

   if(search.source==='Handshake'&&!listing.blocked&&listing.searchKeyword!==search.keyword){listing.blocked='search_keyword_not_applied'}
   metrics.cards_seen+=listing.cards?.length||0;metrics.state='complete';metrics.error=listing.blocked||'';notify(report);
   if(listing.blocked){fail(search.url,listing.blocked);blockedSources.add(search.source);break}
   if(!listing.cards?.length){if(listing.emptyResults)records.push({sourceUrl:search.url,status:'search_empty',error:'search_no_results'});else fail(search.url,listing.loading?'search_loading_timeout':'search_layout_unsupported');break}
   for(const card of listing.cards){
    if(kept>=quota||records.filter(x=>x.status==='fetched').length>=task.limit||Date.now()>deadline)break;
    if(card.title&&!target(card.title)){metrics.title_skips++;notify(report);continue;}
    const id=card.url.match(/\/(\d+)\/?$/)?.[1];const key=(search.source==='LinkedIn'?'linkedin:':'handshake:')+id;
    if(seen.has(key)){metrics.duplicates++;notify(report);continue;}seen.add(key);
    const detail=await read(tabId,card.url);
    if(detail.blocked){fail(card.url,detail.blocked);blockedSources.add(search.source);break}
    if(!detail.title||!detail.company||detail.text?.length<200){fail(card.url,'incomplete_jd_or_layout_unsupported: '+JSON.stringify({...(detail.diagnostics||{}),card_title:(card.title||'').slice(0,120)}));if(records.filter(x=>x.status==='needs_human').length>=6)break;continue}
    if(!target(detail.title)){metrics.title_skips++;notify(report);continue;}
    records.push({sourceUrl:card.url,status:'fetched',title:detail.title,company:detail.company,location:detail.location,text:detail.text.slice(0,80000),search_keyword:search.keyword,application_url:detail.applicationLinks?.length===1?detail.applicationLinks[0]:''});kept++;metrics.fetched++;notify(report);
   }
   if(blockedSources.has(search.source))break;
   }
  }
  if(!records.length)records.push({sourceUrl:task.searches[0].url,status:'search_empty',error:'no_new_jobs'});
 }catch(e){fail('https://www.linkedin.com/jobs/search/','browser_connection_failed')}
 finally{if(tabId!==undefined)try{await chrome.tabs.remove(tabId)}catch{}busy=false}
 return {records,attempted,report};
}
async function resolveEntry(url){
 if(busy)throw Error('browser_busy');if(!allowed(url))throw Error('unsupported_source');busy=true;let id;
 try{id=(await chrome.tabs.create({url:'about:blank',active:false})).id;const result=await read(id,url,'entry');return {links:result.applicationLinks||[],error:result.blocked||''}}
 finally{if(id!==undefined)try{await chrome.tabs.remove(id)}catch{}busy=false}
}
// Read-only liveness check for queued jobs: opens each source page in one background tab, never clicks.
async function checkOpen(urls){
 if(busy)throw Error('browser_busy');if(!Array.isArray(urls)||urls.length>25)throw Error('invalid_task');busy=true;let id;const results=[];
 try{id=(await chrome.tabs.create({url:'about:blank',active:false})).id;
  for(const url of urls){
   if(!allowed(url)){results.push({url,state:'unknown',error:'unsupported_source'});continue}
   try{const r=await read(id,url,'check');results.push({url,state:r.blocked?'unknown':r.closed?'closed':r.title?'open':'unknown',error:r.blocked||''});if(r.blocked)break}
   catch(e){results.push({url,state:'unknown',error:e.message})}
   await pause(1500);
  }
 }finally{if(id!==undefined)try{await chrome.tabs.remove(id)}catch{}busy=false}
 return {results};
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
 if(sender.url!=='http://127.0.0.1:8765/')return false;
 if(message.type==='check-open'){checkOpen(message.urls).then(reply).catch(e=>reply({error:e.message,results:[]}));return true;}
 if(message.type==='resolve-entry'){resolveEntry(message.url).then(reply).catch(e=>reply({error:e.message,links:[]}));return true;}
 if(message.type!=='discover')return false;
 discover(message.task,report=>chrome.tabs.sendMessage(sender.tab.id,{type:'discovery-progress',id:message.task.id,report}).catch(()=>{})).then(reply).catch(()=>reply({error:'browser_task_failed'}));return true;
});
