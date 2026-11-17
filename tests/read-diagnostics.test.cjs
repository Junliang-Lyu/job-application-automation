// A page that never yields a JD is retried the usual 12 times and the failure carries diagnostics.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const c={URL,Set,Date,Math,Number,Array,RegExp,Error,Promise,JSON,setTimeout,importScripts(){},chrome:{runtime:{onMessage:{addListener(){}}},
  tabs:{async update(){},async get(){return {url:'https://www.linkedin.com/jobs/view/123/',status:'complete'}}},
  scripting:{async executeScript(){return [{result:{title:'',company:'',text:'',loading:true,diagnostics:{jdLength:0,readyState:'complete'}}}]}}}};
vm.createContext(c);vm.runInContext(fs.readFileSync('browser-extension/jobsearch-bridge/worker.js','utf8'),c);c.pause=async()=>{};c.readJobPage=()=>{};c.expandJobDescription=()=>{};
(async()=>{
  const r=await c.read(1,'https://www.linkedin.com/jobs/view/123/');
  const d=r.diagnostics;
  assert.equal(d.attempts,12,'same 12 attempts as before (behaviour unchanged)');
  assert.equal(d.tab_status,'complete');assert.equal(d.final_path,'www.linkedin.com/jobs/view/123/');
  assert.equal(d.loading,true);assert.equal(d.jdLength,0);assert.equal(d.readyState,'complete');
  assert.ok(typeof d.waited_ms==='number');
  console.log('PASS: failed read records attempts, tab state, final page and loading flag');
})().catch(e=>{console.error(e);process.exitCode=1});
