#!/usr/bin/env node
'use strict';
// Optional product-specific Playwright adapter. Selected ChatGPT runtime must actually run it.
// No AI model calls, production installs, source writes, or release-gate impersonation.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');

function parseArgs(argv) {
  const opts={};
  for(let i=0;i<argv.length;i+=2) {
    const k=argv[i],v=argv[i+1];
    if(!['--mission','--out','--frozen-sha256'].includes(k)||!v||Object.hasOwn(opts,k))
      throw Error('Usage: node parser-studio-browser.cjs --mission FILE --out NEW_DIRECTORY --frozen-sha256 64hex');
    opts[k]=v;
  }
  if(Object.keys(opts).length!==3)throw Error('Missing required argument');
  return opts;
}
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const args=parseArgs(process.argv.slice(2));
const raw=fs.readFileSync(args['--mission']);
if(sha(raw)!==args['--frozen-sha256'])throw Error('frozen requirements oracle SHA mismatch');
const mission=JSON.parse(raw.toString('utf8'));
if(mission.classification!=='SYNTHETIC_SECURITY_EVENTS_NO_CUSTOMER_DATA')throw Error('unsafe non-synthetic mission');
if(!Array.isArray(mission.input_records)||mission.input_records.length<1||mission.input_records.length>30)throw Error('invalid bounded records');
const site=new URL(mission.app);
if(!(site.href==='https://app.parser.xdr.ooo/' ||
  (site.protocol==='http:'&&['127.0.0.1','localhost'].includes(site.hostname))))
  throw Error('isolated non-production development URL only');
const out=path.resolve(args['--out']);
if(fs.existsSync(out))throw Error('output directory already exists; never overwrite evidence');
fs.mkdirSync(out,{recursive:true,mode:0o700});
let pw;
try{pw=require('playwright');}catch(_){throw Error('playwright package required from product checkout via NODE_PATH; no automatic install');}
async function main(){
 const browser=await pw.chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1360,height:900},acceptDownloads:true});
 const page=await context.newPage();
 const errors=[],thirdparty=[],responses=[],network=[],artifacts=[];
 page.on('pageerror',e=>errors.push(String(e).slice(0,400)));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text().slice(0,400))});
 page.on('request',req=>{const u=new URL(req.url());if(!['127.0.0.1','localhost','app.parser.xdr.ooo'].includes(u.hostname))
    thirdparty.push({host:u.hostname,method:req.method()})});
 page.on('response',async r=>{
   if(!r.url().includes('/api/'))return;
   const entry={url:r.url(),status:r.status()};
   try{
     const bytes=await r.body();
     entry.sha256=sha(bytes);
     if(r.url().endsWith('/api/v1/multipass/preview-batch'))entry.body=bytes.toString('utf8').slice(0,1024*1024);
   }catch(e){entry.error=String(e)}
   responses.push(entry);network.push({path:new URL(r.url()).pathname,status:r.status()});
 });
 await context.tracing.start({screenshots:true,snapshots:true,sources:false});
 try{
   const html=await page.goto(site.href,{waitUntil:'domcontentloaded',timeout:30000});
   if(!html||html.status()!==200)throw Error('public site not ready');
   const logs=mission.input_records.map(x=>JSON.stringify(x)).join('\n');
   const input=page.getByLabel('Auto Parser raw logs');
   await input.fill(logs);
   if(await input.inputValue()!==logs)throw Error('source paste fidelity mismatch');
   await page.getByRole('button',{name:'Analyze Logs',exact:true}).click();
   await page.getByLabel('Beginner analysis summary').waitFor({state:'visible',timeout:25000});
   const summary=await page.getByLabel('Beginner analysis summary').innerText();
   const accounting=await page.getByLabel('Automatic event accounting').innerText();
   await page.screenshot({path:path.join(out,'after-analysis.png'),fullPage:true});
   const review=page.getByRole('button',{name:'Review Fields',exact:true});
   if(!await review.isEnabled())throw Error('parser not reviewable: '+summary);
   await review.click();
   await page.getByLabel('Review Fields stage').waitFor({state:'visible',timeout:25000});
   await page.screenshot({path:path.join(out,'review-fields.png'),fullPage:true});
   await page.getByLabel('Review Fields stage').getByRole('button',{name:'Map Fields',exact:true}).click();
   await page.getByLabel('Map Fields stage').waitFor({state:'visible',timeout:25000});
   await page.getByLabel('Map Fields stage').getByRole('button',{name:'Create Parser',exact:true}).click();
   await page.getByLabel('Create Parser stage').waitFor({state:'visible',timeout:25000});
   const create=page.getByLabel('Create Parser Files');
   if(!await create.isEnabled())throw Error('draft creation button disabled');
   await create.click();
   const panel=page.getByLabel('Generated parser files');
   await panel.waitFor({state:'visible',timeout:30000});
   const generated=await panel.innerText();
   if(!generated.toLowerCase().includes('created successfully'))throw Error('draft generation failed');
   const buttons=await panel.getByRole('button').all();
   let count=0;
   for(const b of buttons){
     if(!(await b.innerText()).includes('customized_'))continue;
     const promise=page.waitForEvent('download',{timeout:15000});
     await b.click();
     const download=await promise, stream=await download.createReadStream();
     const chunks=[];for await(const x of stream)chunks.push(Buffer.from(x));
     const data=Buffer.concat(chunks);
     const item={filename:download.suggestedFilename(),bytes:data.length,sha256:sha(data),path:'generated-'+count+'.bin'};
     fs.writeFileSync(path.join(out,item.path),data,{mode:0o600});
     artifacts.push(item);
     if(++count===2)break;
   }
   await page.screenshot({path:path.join(out,'draft-complete.png'),fullPage:true});
   fs.writeFileSync(path.join(out,'capture.json'),JSON.stringify({
     mission_sha256:sha(raw),html_status:html.status(),input_sha256:sha(Buffer.from(logs,'utf8')),
     summary,accounting,responses,errors,thirdparty},null,2),{mode:0o600});
   fs.writeFileSync(path.join(out,'draft-run.json'),JSON.stringify({
     mission_sha256:sha(raw),source_sha256:sha(Buffer.from(logs,'utf8')),
     generated_panel:generated,artifacts,errors,network,finished_at:new Date().toISOString()
   },null,2),{mode:0o600});
   if(errors.length||thirdparty.length||artifacts.length!==2)throw Error('browser evidence incomplete');
   console.log(JSON.stringify({kind:'independent-ai-qa-live-browser',status:'CAPTURED_NOT_QUALIFIED',
     mission_sha256:sha(raw),artifacts:artifacts.map(a=>a.filename),evidence_path:out,
     note:'must separately run tools/ai_qa_parser_studio_oracle.py; not a release gate'}));
 }finally{
   await context.tracing.stop({path:path.join(out,'chromium-trace.zip')});
   await browser.close();
 }
}
main().catch(e=>{console.error('QA_CAPTURE_FAILED',String(e.stack||e).slice(0,1500));process.exitCode=2});
