// LinkedIn renders no JD in a background tab: fall back to the public posting, then to a brief foreground switch.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function load({guestText,pageTextWhenActive}){
  const calls=[];let active=false;
  const c={URL,Set,Date,Math,Number,Array,RegExp,Error,Promise,JSON,setTimeout,importScripts(){},chrome:{runtime:{onMessage:{addListener(){}}},
    tabs:{async update(id,p){if('active' in p){calls.push(['active',id]);active=id===1}},async get(){return {url:'https://www.linkedin.com/jobs/view/123/',status:'complete',windowId:7}},
          async query(){return [{id:99}]}},
    scripting:{async executeScript({func}){
      if(func.name==='fetchLinkedInGuestJD'){calls.push(['guest']);return [{result:guestText?{status:'ok',text:guestText}:{status:'http_404',text:''}}]}
      const text=active&&pageTextWhenActive?pageTextWhenActive:'';
      return [{result:{title:'ML Engineer',company:'Quora',location:'Remote',text,loading:!text,diagnostics:{jdLength:text.length}}}]}}}};
  vm.createContext(c);vm.runInContext(fs.readFileSync('browser-extension/jobsearch-bridge/worker.js','utf8'),c);
  c.pause=async()=>{};c.readJobPage=function readJobPage(){};c.fetchLinkedInGuestJD=function fetchLinkedInGuestJD(){};c.expandJobDescription=()=>{};
  return {c,calls};
}
(async()=>{
  const jd='We build ML systems. '.repeat(20);
  let {c,calls}=load({guestText:jd});
  let r=await c.read(1,'https://www.linkedin.com/jobs/view/123/');
  assert.equal(r.text,jd);assert.equal(r.diagnostics.jd_source,'guest_api');
  assert.ok(!calls.some(x=>x[0]==='active'),'no focus change when the public posting works');

  ({c,calls}=load({guestText:'',pageTextWhenActive:jd}));
  r=await c.read(1,'https://www.linkedin.com/jobs/view/123/');
  assert.equal(r.text,jd);assert.equal(r.diagnostics.jd_source,'foreground');assert.equal(r.diagnostics.guest_status,'http_404');
  assert.deepEqual(calls.filter(x=>x[0]==='active'),[['active',1],['active',99]],'brought forward once, user tab restored');

  ({c,calls}=load({guestText:'',pageTextWhenActive:''}));
  r=await c.read(1,'https://www.linkedin.com/jobs/view/123/');
  assert.equal(r.diagnostics.jd_source,'none');assert.equal(r.diagnostics.attempts,12);
  assert.deepEqual(calls.filter(x=>x[0]==='active').at(-1),['active',99],'user tab restored even on failure');
  console.log('PASS: background LinkedIn JD → public posting fallback → brief foreground fallback, user tab restored');
})().catch(e=>{console.error(e);process.exitCode=1});
