#!/usr/bin/env node
'use strict';
/**
 * Persistent real-Chromium, model-in-the-loop exploratory QA transport.
 * It NEVER synthesizes AI choices: the external selected AI must inspect each
 * live observation and send the next JSON action. No autonomous model API
 * connection, release gate or trusted verification actor is implied.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const contract = require('./autonomous-explorer-contract.cjs');
const {sha256,validateMission,allowRequest,approveAction,approveFinish,ContractError} = contract;

function argvOptions(argv) {
  const allowed = ['--mission','--expected-plan-sha256','--expected-html-sha256','--out'];
  if (argv.length !== 8) throw new ContractError('INVALID_CLI_ARGS');
  const a = {};
  for (let i=0;i<argv.length;i+=2) {
    if (!allowed.includes(argv[i]) || !argv[i+1] || Object.hasOwn(a, argv[i])) {
      throw new ContractError('INVALID_CLI_ARGS');
    }
    a[argv[i]] = argv[i+1];
  }
  if (allowed.some(key=>!Object.hasOwn(a,key))) throw new ContractError('INVALID_CLI_ARGS');
  if (!path.isAbsolute(a['--out']) ||
      !/^[a-f0-9]{64}$/.test(a['--expected-html-sha256'])) {
    throw new ContractError('UNPINNED_RUNTIME');
  }
  if (fs.existsSync(a['--out'])) throw new ContractError('REFUSE_OVERWRITE_EVIDENCE');
  return a;
}
function safeString(value, max=160) {
  return String(value || '').replace(/[\u0000-\u001f]+/g,' ').slice(0,max);
}
function accessText(node) {
  const tag = (node.tagName||'').toLowerCase();
  const kind = node.isContentEditable ? 'contenteditable'
    : tag === 'textarea' ? 'textarea' : tag === 'input' ? 'input' : tag;
  const label = node.getAttribute('aria-label') || node.getAttribute('title') ||
    (node.labels && node.labels.length && node.labels[0].innerText) ||
    node.getAttribute('placeholder') || node.getAttribute('name') ||
    node.innerText || node.textContent || '';
  const rect = node.getBoundingClientRect();
  const style = getComputedStyle(node);
  // Off-viewport controls must not consume a model's bounded control list.
  // After an explicit scroll, a new observation provides fresh visible refs.
  const withinViewport = rect.width > 0 && rect.height > 0 && rect.bottom > 0
    && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
  return {kind, name: String(label).replace(/[\r\n\t]+/g,' ').trim().slice(0,130),
    input_type: (node.getAttribute('type')||'').toLowerCase(),
    visible: withinViewport && style.visibility!=='hidden' && style.display!=='none',
    focused: node===document.activeElement,
    options: tag==='select' ? Array.from(node.options||[]).slice(0,30)
      .map(opt=>({value:String(opt.value).slice(0,100),label:String(opt.textContent).trim().slice(0,90),disabled:!!opt.disabled})) : [],
    enabled: !node.disabled && node.getAttribute('aria-disabled') !== 'true',
    value_length: typeof node.value === 'string' ? node.value.length : null};
}
function elementSourceKind(node) { return node.kind; }
function observationDigest(payload, nonce) {
  return sha256(Buffer.from(nonce+':'+JSON.stringify(payload)));
}
function logRecord(out, rec) {
  fs.appendFileSync(path.join(out,'events.jsonl'),JSON.stringify(rec)+'\n',{mode:0o600});
}
function emit(type, obj) {
  process.stdout.write('AIQA_'+type+'='+JSON.stringify(obj)+'\n');
}

async function run() {
  const args=argvOptions(process.argv.slice(2));
  const raw=fs.readFileSync(args['--mission']);
  const mission=validateMission(raw,args['--expected-plan-sha256']);
  const nodePath=process.env.PLAYWRIGHT_MODULE_ROOT||process.cwd();
  const playwright=require(require.resolve('playwright',{paths:[nodePath]}));
  fs.mkdirSync(args['--out'],{recursive:true,mode:0o700});
  const out=args['--out'];
  fs.writeFileSync(path.join(out,'mission-pin.json'),JSON.stringify({
    kind:'ai-qa-mission-pin',mission_sha256:sha256(raw),
    frozen_runtime_html_sha256:args['--expected-html-sha256'],
    goal:mission.goal,origin:mission.origin,
    note:'Frozen pre-execution identity, NOT a release trust anchor'
  },null,2)+'\n',{mode:0o600});

  const browser=await playwright.chromium.launch({headless:true});
  const ctx=await browser.newContext({viewport:{width:1365,height:900},
    acceptDownloads:false,permissions:[],serviceWorkers:'block'});
  const page=await ctx.newPage();
  const sessionId=crypto.randomUUID();
  const denied=[], errors=[], responses=[], popups=[];
  let steps=0, observationCount=0, latest=null, elements=new Map();
  let runtimeHtml=null, phase='INITIALIZING', stopped=false;
  const startedAt=new Date().toISOString();

  ctx.on('page',async other=>{
    if(other!==page){popups.push('UNSOLICITED_POPUP_BLOCKED');
      try{await other.close()}catch(_){/* page already closed */}}
  });
  await ctx.route('**/*',async route=>{
    const req=route.request();
    const verdict=allowRequest(mission,req.url(),req.method());
    if(!verdict.allowed) {
      const item={method:req.method(),reason:verdict.reason,
        path:safeString((()=>{try{return new URL(req.url()).pathname}catch(_){return 'INVALID'}})(),160)};
      denied.push(item);
      try{await route.abort('blockedbyclient')}catch(_){/* navigation might close */ }
      return;
    }
    try{await route.continue()}catch(_){/* browser request cancelled */ }
  });
  page.on('pageerror',err=>errors.push({kind:'PAGE_ERROR',message:safeString(err.message,200)}));
  page.on('console',msg=>{if(msg.type()==='error')
    errors.push({kind:'CONSOLE_ERROR',message:safeString(msg.text(),200)})});
  page.on('response',res=>{
    let u;try{u=new URL(res.url())}catch(_){return}
    if(responses.length<250)responses.push({method:res.request().method(),
      path:u.pathname.slice(0,130),status:res.status()});
  });
  await ctx.tracing.start({screenshots:true,snapshots:true,sources:false});
  async function observe(reason) {
    const handles=await page.locator('button,a,input,textarea,select,[role="button"],[role="tab"],[contenteditable="true"]').elementHandles();
    elements=new Map();
    const listing=[];
    for(const handle of handles) {
      if(listing.length>=75)break;
      try{
        const meta=await handle.evaluate(accessText);
        if(!meta.visible)continue;
        const ref='e'+String(listing.length+1).padStart(3,'0');
        elements.set(ref,handle);
        listing.push({ref,...meta,name:safeString(meta.name,130)});
      }catch(_){/* detached nodes disappear from this snapshot */}
    }
    let body='';
    try{body=await page.locator('body').innerText({timeout:3500})}catch(_){/* page may navigate */}
    let headings=[];
    try{headings=(await page.locator('h1,h2,h3').allTextContents()).slice(0,20)
      .map(t=>safeString(t,160))}catch(_){/* route may be transitioning */}
    observationCount+=1;
    const screenshotName='observation-'+String(observationCount).padStart(3,'0')+'.png';
    let screenshotDigest=null;
    try {
      const buf=await page.screenshot({path:path.join(out,screenshotName),
        fullPage:false,animations:'disabled',timeout:9000});
      screenshotDigest=sha256(buf);
    } catch(e) {errors.push({kind:'SCREENSHOT_ERROR',message:safeString(e.message,150)})}
    const payload={
      ordinal:observationCount,after:reason,actions_used:steps,
      actions_remaining:mission.max_actions-steps,
      url:safeString(page.url(),180),
      title:safeString(await page.title().catch(()=>''),160),
      headings,body_excerpt:safeString(body,5500),
      body_sha256:sha256(Buffer.from(body)),
      elements:listing,denied_requests:denied.slice(-10),
      console_errors:errors.slice(-12),popups:popups.slice(-6),
      recent_http:responses.slice(-16),
      screenshot:screenshotDigest ? {name:screenshotName,sha256:screenshotDigest}:null,
    };
    const id=observationDigest(payload,sessionId);
    latest={id,...payload};
    logRecord(out,{kind:'observation',id,observation:payload});
    emit('OBSERVATION',{kind:'real-browser-observation',authority:'T0_ADVISORY_ONLY',
      runtime_html_sha256:runtimeHtml,mission_sha256:sha256(raw),...latest});
  }
  async function action(cmd){
    const validated=approveAction(cmd,latest,steps,mission);
    const a=validated.action;
    const before=latest.id, networkStart=denied.length, errorStart=errors.length;
    steps+=1;latest=null;
    let problem=null;
    try {
      if(a.kind==='scroll')await page.mouse.wheel(0,a.direction==='down'?620:-620);
      else if(a.kind==='back')await page.goBack({waitUntil:'domcontentloaded',timeout:10000});
      else{
        const handle=elements.get(a.ref);
        if(!handle || !(await handle.evaluate(el=>el.isConnected))) {
          throw new ContractError('DETACHED_ELEMENT');
        }
        if(a.kind==='click')await handle.click({timeout:9500});
        else if(a.kind==='fill')await handle.fill(a.text,{timeout:9500});
        else if(a.kind==='press')await handle.press(a.key,{timeout:9500});
        else if(a.kind==='select')await handle.selectOption(a.value,{timeout:9500});
      }
      await page.waitForTimeout(500);
    }catch(e){problem=e.code||safeString(e.message,160)}
    const actionLog={kind:'ai_selected_action',number:steps,observation_id:before,
      action: a.kind==='fill'?{kind:a.kind,ref:a.ref,
        input_sha256:sha256(Buffer.from(a.text)),input_length:a.text.length}:a,
      model_reason:cmd.reason,
      action_error:problem,denied_new_requests:denied.slice(networkStart),
      new_errors:errors.slice(errorStart)};
    logRecord(out,actionLog);
    emit('ACTION_RESULT',{kind:'model_selected_action_result',...actionLog});
    await observe('action-'+steps);
  }
  async function finish(cmd,forcedReason=null) {
    if(stopped)return;
    if(forcedReason===null)approveFinish(cmd,latest,steps);
    stopped=true;
    phase='FINISHED';
    const report={
      kind:'independent-ai-autonomous-exploration',authority:'T0_ADVISORY_ONLY',
      actual_model_invocation_verified:false,
      human_equivalent_release_gate:false,release_authorized:false,
      product_full_user_e2e_verified:false,
      acting_runtime:'CHATGPT_CHAT_INTERACTIVE_AI',
      session_id:sessionId,mission_sha256:sha256(raw),
      runtime_html_sha256:runtimeHtml,
      browser_version:browser.version(),
      started_at:startedAt,finished_at:new Date().toISOString(),
      actual_model_selected_actions:steps,observations:observationCount,
      model_disposition:forcedReason?'BLOCKED':cmd.disposition,
      model_rationale:forcedReason||cmd.rationale,
      oracle_status:forcedReason?{}:cmd.oracle_status,
      claimed_findings:forcedReason?[]:cmd.finding_ids,
      denied_network_requests:denied,
      browser_errors:errors,popup_events:popups,
      trace_sha256:null,
    };
    try{await ctx.tracing.stop({path:path.join(out,'chromium-trace.zip')});
      report.trace_sha256=sha256(fs.readFileSync(path.join(out,'chromium-trace.zip')));
    }catch(e){report.trace_error=safeString(e.message)}
    fs.writeFileSync(path.join(out,'final.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600});
    logRecord(out,{kind:'session_finished',status:report.model_disposition,
      evidence_sha256:sha256(fs.readFileSync(path.join(out,'final.json')))});
    emit('FINAL',{...report,final_report_sha256:sha256(fs.readFileSync(path.join(out,'final.json')))});
    await browser.close();
    process.exitCode=forcedReason?3:0;
  }
  try{
    const rootResponse=await page.goto(mission.target,{waitUntil:'domcontentloaded',timeout:22000});
    if(!rootResponse || rootResponse.status()!==200)throw new ContractError('PUBLIC_ENTRY_NOT_READY');
    runtimeHtml=sha256(await rootResponse.body());
    if(runtimeHtml!==args['--expected-html-sha256'])
      throw new ContractError('RUNTIME_HTML_DIGEST_MISMATCH');
    phase='READY';
    emit('READY',{session_id:sessionId,kind:'interactive-real-browser-qa',
      authority:'T0_ADVISORY_ONLY',mission_sha256:sha256(raw),runtime_html_sha256:runtimeHtml,
      max_actions:mission.max_actions,goal:mission.goal,
      expected_outcomes:mission.expected_outcomes,
      actual_model_invocation_verified:false});
    await observe('initial-navigation');
    const rl=readline.createInterface({input:process.stdin,crlfDelay:Infinity,terminal:false});
    for await(const line of rl){
      if(stopped)break;
      if(!line.trim())continue;
      let cmd;
      try{cmd=JSON.parse(line)}catch(_){emit('REJECTED',{code:'INVALID_JSON_COMMAND'});continue}
      try{
        if(cmd.cmd==='observe'){
          if(Object.keys(cmd).length!==1)throw new ContractError('INVALID_OBSERVE_COMMAND');
          await observe('explicit-model-observe');
        }else if(cmd.cmd==='act')await action(cmd);
        else if(cmd.cmd==='finish'){await finish(cmd);break}
        else throw new ContractError('UNKNOWN_COMMAND');
      }catch(e){
        emit('REJECTED',{code:e.code||'BROWSER_ACTION_ERROR',
          message:safeString(e.message,220),current_observation_id:latest?.id||null});
        logRecord(out,{kind:'rejected_model_command',code:e.code||'BROWSER_ACTION_ERROR'});
      }
    }
    if(!stopped)await finish(null,'NO_VERIFIED_AI_FINISH');
  }catch(e) {
    if(!stopped){
      emit('BLOCKED',{code:e.code||'BROWSER_INITIALIZATION_ERROR',
        message:safeString(e.message,200)});
      await finish(null,e.code||'BROWSER_INITIALIZATION_ERROR');
    }
  }
}
if(require.main===module)
  run().catch(e=>{console.error('AIQA_BOOTSTRAP_BLOCKED='+safeString(e.code||e.message));
    process.exitCode=3});
module.exports={argvOptions,accessText,observationDigest};
