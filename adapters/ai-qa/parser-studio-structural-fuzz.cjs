#!/usr/bin/env node
'use strict';
// Seeded property/metamorphic security-log source-fidelity fuzzing.
// Uses actual Chromium against the development site. Never uploads customer logs.
const fs=require('fs'),crypto=require('crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function args(v){const o={};for(let i=0;i<v.length;i+=2){if(!['--seed','--cases','--out'].includes(v[i])||!v[i+1])throw Error('usage: --seed int --cases 1..100 --out NEW_REPORT');o[v[i]]=v[i+1]}return o}
const opts=args(process.argv.slice(2));
const seed=Number(opts['--seed']),count=Number(opts['--cases']);
if(!Number.isSafeInteger(seed)||seed<1||!Number.isInteger(count)||count<1||count>100||!opts['--out']||fs.existsSync(opts['--out']))throw Error('invalid seed/cases/output');
let s=seed>>>0;
function rand(){s+=0x6D2B79F5;let t=s;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return ((t^(t>>>14))>>>0)/4294967296}
function pick(xs){return xs[Math.floor(rand()*xs.length)]}
const sample=(caseId,id)=>({
 timestamp:'2026-10-24T12:'+String(caseId%60).padStart(2,'0')+':'+String(id).padStart(2,'0')+'Z',
 event_type:pick(['alert','threat','traffic','dns']),
 action:pick(['blocked','allowed','alert','deny','ALLOW','drop']),
 src_ip:'192.0.2.'+(1+Math.floor(rand()*248)),
 dest_ip:'198.51.100.'+(1+Math.floor(rand()*248)),
 dest_port:pick([1,22,53,80,443,8080,65535]),
 signature:pick(['QA-policy-denied','QA-sig:ABC/123','Suspicious "quoted" event','José 장비 WAF', 'backslash \\ and comma, and :colon']),
});
async function main(){
 let pw;
 try{pw=require('playwright')}catch{throw Error('supply pinned product Playwright through NODE_PATH; no implicit installation')}
 const browser=await pw.chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1280,height:800}});
 const report={kind:'parser-studio-seeded-structural-fuzz',seed,planned_cases:count,
  runtime:'actual chromium, development site',product_url:'https://app.parser.xdr.ooo/',
  authority:'T0_ADVISORY_ONLY',completed_cases:0,requests:0,verified_event_values:0,
  verified_source_spans:0,failures:[],started_at:new Date().toISOString()};
 try{
  const response=await page.goto('https://app.parser.xdr.ooo/',{waitUntil:'domcontentloaded',timeout:30000});
  if(!response||response.status()!==200)throw Error('development website not ready');
  for(let i=0;i<count;i++){
    const first=sample(i,0),second=sample(i,1);
    const variants=[[first,second],[second,first]];const perVariant=[];
    for(const records of variants){
      const raw=records.map(x=>JSON.stringify(x)).join('\n');
      const r=await page.evaluate(async raw=>{
       try{const v=await fetch('/api/v1/multipass/preview-batch',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',body:JSON.stringify({raw})});
       return {status:v.status,data:await v.json()}}
       catch(e){return{error:String(e)}}},raw);
      report.requests++;
      const fail=msg=>{throw Error(msg)};
      try{
        if(r.error||r.status!==200)fail('backend response '+JSON.stringify(r).slice(0,200));
        const b=r.data;
        if(b.contract!=='parser-studio.multipass-source-batch-preview.v0.1'||b.sourceSha256!==sha(Buffer.from(raw))||b.sourceBytes!==Buffer.byteLength(raw))fail('source identity');
        if(b.eventCount!==2||!Array.isArray(b.events)||b.events.length!==2)fail('record preservation');
        if(b.nativeVerified!==false||b.targetVerified!==false)fail('unsupported native verification assertion');
        let off=0;
        for(let k=0;k<2;k++){
          const obj=records[k],rec=b.events[k],rawEvent=JSON.stringify(obj),bytes=Buffer.from(rawEvent);
          if(rec.index!==k||rec.startByte!==off||rec.endByte!==off+bytes.length)fail('record byte boundaries');
          const v=rec.preview;
          if(v.status!=='SOURCE_PARSED'||v.syntax!=='JSON'||v.sourceSha256!==sha(bytes)||v.sourceBytes!==bytes.length)fail('structural record classification/source');
          if(JSON.stringify(v.fields)!==rawEvent)fail('field extraction mismatch');
          if(!Array.isArray(v.spans)||v.spans.length!==Object.keys(obj).length)fail('source span cardinality');
          const keys=new Set();
          for(const sp of v.spans){
            if(!(sp.path in obj)||keys.has(sp.path)||!Number.isInteger(sp.startByte)||!Number.isInteger(sp.endByte)||sp.startByte<0||sp.endByte>bytes.length||sp.startByte>=sp.endByte)fail('invalid field span');
            const serialized=JSON.stringify(obj[sp.path]),sliced=bytes.subarray(sp.startByte,sp.endByte).toString('utf8');
            if(sliced!==serialized)fail('inexact field byte evidence:'+sp.path);
            keys.add(sp.path);report.verified_source_spans++;
          }
          if(keys.size!==Object.keys(obj).length||v.residuals.length||v.issues.length)fail('silently missing source fields');
          report.verified_event_values+=Object.keys(obj).length;
          off+=bytes.length+(k===0?1:0);
        }
        if(off!==Buffer.byteLength(raw))fail('unaccounted stream bytes');
        perVariant.push(b.events.map(x=>x.preview.fields));
      }catch(err){
        report.failures.push({case_id:i,variant:perVariant.length,seed,reason:String(err.message),
         source_sha256:sha(Buffer.from(raw)),source_records:records});
      }
    }
    if(perVariant.length===2&&JSON.stringify(perVariant[0])!==JSON.stringify(perVariant[1].reverse()))
      report.failures.push({case_id:i,reason:'metamorphic record reorder changed field values'});
    else if(perVariant.length===2)report.completed_cases++;
  }
 }finally{report.finished_at=new Date().toISOString();fs.writeFileSync(opts['--out'],JSON.stringify(report,null,2),{mode:0o600});await browser.close()}
 console.log(JSON.stringify({kind:report.kind,seed,planned_cases:count,completed_cases:report.completed_cases,
  requests:report.requests,verified_event_values:report.verified_event_values,
  verified_source_spans:report.verified_source_spans,failures:report.failures.length,
  authority:'T0_ADVISORY_ONLY',result:report.failures.length?'PROPERTY_FAIL':'PROPERTY_PASS'}));
 if(report.failures.length)process.exitCode=2;
}
main().catch(e=>{console.error('PROPERTY_CAMPAIGN_ERROR',String(e.stack||e).slice(0,1600));process.exitCode=3});
