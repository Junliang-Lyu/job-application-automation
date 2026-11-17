const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
async function trial(repeated=false){
 const visits=[];const detailVisits=[];
 const context={URL,Set,Date,Math,Number,Array,RegExp,Error,Promise,setTimeout,importScripts(){},chrome:{runtime:{onMessage:{addListener(){}}},tabs:{async create(){return{id:7}},async remove(id){assert.equal(id,7)}}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('browser-extension/jobsearch-bridge/worker.js','utf8'),context);
 context.read=async(id,url)=>{
 const u=new URL(url);
 if(u.pathname==='/job-search'){
 visits.push(Number(u.searchParams.get('page')));
 const ids=repeated||u.searchParams.get('page')==='1'?[10,11]:[12,13];
 return{searchKeyword:'backend',cards:ids.map(n=>({url:'https://smu.joinhandshake.com/job-search/'+n,title:'Backend Engineer'}))};
 }
 detailVisits.push(url);return{title:'Backend Engineer',company:'Acme',location:'Austin, TX',text:'JD '.repeat(100)};
 };
 const task={searches:[{source:'Handshake',keyword:'backend',url:'https://smu.joinhandshake.com/job-search?query=backend'}],excluded:['handshake:10','handshake:11'],limit:2,max_pages:3};
 const result=await context.discover(task);
 assert.deepEqual(visits,[1,2]);
 assert.equal(result.report.queries[0].duplicates,2);
 assert.equal(result.report.queries[0].pages_read,2);
 assert.equal(detailVisits.length,repeated?0:2);
 if(repeated)assert.equal(result.report.queries[0].error,'pagination_not_applied');
 else assert.equal(result.records.filter(r=>r.status==='fetched').length,2);
}
(async()=>{await trial();await trial(true);console.log('PASS: historical jobs skipped before JD reading; page 2 fills quota; unchanged pages stop.');})().catch(e=>{console.error(e);process.exitCode=1});
