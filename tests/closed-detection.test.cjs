// Closed-posting detection reads only the region after the job title.
const assert=require('assert');
function page(body,title){
  global.location={hostname:'www.linkedin.com',pathname:'/jobs/view/123/'};
  global.document={title:title+' | Acme | LinkedIn',body:{innerText:body},querySelector:()=>null,querySelectorAll:()=>[]};
  delete require.cache[require.resolve('../browser-extension/jobsearch-bridge/extract.js')];
  return require('../browser-extension/jobsearch-bridge/extract.js').readJobPage();
}
const nav='Home My Network Jobs Messaging '.repeat(5);
let r=page(nav+'Backend Engineer\nAcme · Dallas, TX\nNo longer accepting applications\nAbout the job ...','Backend Engineer');
assert.strictEqual(r.closed,true,'top-card closed notice detected');
r=page(nav+'Backend Engineer\nAcme · Dallas, TX\nEasy Apply\nAbout the job '+'x'.repeat(2000)+' Similar jobs: Data Engineer No longer accepting applications','Backend Engineer');
assert.strictEqual(r.closed,false,'sidebar notice far below title is ignored');
console.log('PASS: closed posting detected near title; distant sidebar text ignored');
