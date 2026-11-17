// Runs only in the local workbench tab in the profile where this extension is installed.
if(location.origin==='http://127.0.0.1:8765' && document.querySelector('meta[name="workbench-token"]')){
 document.documentElement.dataset.jobsearchEntry='v1';
 document.documentElement.dataset.jobsearchCheck='v1';
 document.documentElement.dataset.jobsearchRounds='v1';
 document.addEventListener('workbench-resolve-entry',event=>{
   const reply=(links,error)=>document.dispatchEvent(new CustomEvent('workbench-entry-result',{detail:{id:event.detail?.id,links,error}}));
   try{chrome.runtime.sendMessage({type:'resolve-entry',url:event.detail?.url},response=>{
     const error=chrome.runtime.lastError;reply(response?.links||[],error?'extension_interrupted':!response?'extension_handler_missing':response.error||'');
   })}catch{reply([],'extension_interrupted')}
 });
 document.addEventListener('workbench-check-open',event=>{
   const reply=(results,error)=>document.dispatchEvent(new CustomEvent('workbench-check-result',{detail:{id:event.detail?.id,results,error}}));
   try{chrome.runtime.sendMessage({type:'check-open',urls:event.detail?.urls},response=>{
     const error=chrome.runtime.lastError;reply(response?.results||[],error?'extension_interrupted':!response?'extension_handler_missing':response.error||'');
   })}catch{reply([],'extension_interrupted')}
 });
 const client=crypto.randomUUID();let pending=null;
 let token=document.querySelector('meta[name="workbench-token"]').content;
 async function post(path,data){
   const send=()=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify(data)});
   let response=await send();
   if(response.status===403&&(await response.clone().json()).code==='session_expired'){
     const session=await fetch('/api/session',{headers:{'X-Workbench-Session':'refresh'}});
     if(session.ok){token=(await session.json()).token;document.querySelector('meta[name="workbench-token"]').content=token;response=await send()}
   }
   if(!response.ok)throw Error('连接失效，请刷新工作台');return response.json()
 }
 chrome.runtime.onMessage.addListener(message=>{if(message.type==='discovery-progress')post('/api/bridge/progress',{id:message.id,client,report:message.report}).catch(()=>{})});
 async function deliver(){if(!pending)return;await post('/api/bridge/result',pending);pending=null}
 // After the extension is reloaded, this old copy of the script can no longer reach it: stop polling
 // so the workbench reports "not connected" instead of handing it tasks that never finish.
 const extensionAlive=()=>{try{return !!chrome.runtime?.id}catch{return false}};
 function send(message,callback){
   if(!extensionAlive())throw Error('extension_reloaded');
   chrome.runtime.sendMessage(message,callback);
 }
 async function poll(){
   if(!extensionAlive()){document.documentElement.dataset.jobsearchBridge='disconnected';return}
   try{
   await deliver();const {task}=await post('/api/bridge/poll',{client});
   document.documentElement.dataset.jobsearchBridge='connected';
   if(task)try{
   if(task.kind==='check')send({type:'check-open',urls:task.urls},async response=>{
     const error=chrome.runtime.lastError;
     pending={id:task.id,client,check_results:response?.results||[],error:error?'extension_interrupted':response?.error||''};
     try{await deliver()}catch{}
   });
   else send({type:'discover',task},async response=>{
     const error=chrome.runtime.lastError;
     pending={id:task.id,client,records:response?.records||[{sourceUrl:'https://www.linkedin.com/jobs/search/',status:'needs_human',error:error?'extension_interrupted':response?.error||'extension_failed'}],attempted:response?.attempted||[],report:response?.report||{queries:[]}};
     try{await deliver()}catch{}
   });
   }catch(e){
     pending=task.kind==='check'?{id:task.id,client,check_results:[],error:'extension_reloaded'}
       :{id:task.id,client,records:[{sourceUrl:'https://www.linkedin.com/jobs/search/',status:'needs_human',error:'extension_interrupted'}],attempted:[],report:{queries:[]}};
     try{await deliver()}catch{}
   }
 }catch{document.documentElement.dataset.jobsearchBridge='disconnected'}finally{setTimeout(poll,3000)}}
 poll();
}
