#!/usr/bin/env node
'use strict';
/* Adversarial tests: no fake AI choice, release PASS, unpinned oracle, external writes. */
const test=require('node:test');
const assert=require('node:assert/strict');
const {sha256,validateMission,allowRequest,approveAction,approveFinish,ContractError}
  =require('../adapters/ai-qa/autonomous-explorer-contract.cjs');
const {argvOptions,observationDigest,accessText}=require('../adapters/ai-qa/autonomous-browser-explorer.cjs');

function source(overrides={}) {
  return {
    schema_version:1,kind:'independent-ai-browser-mission',
    classification:'SYNTHETIC_PUBLIC_DEVELOPMENT_ONLY',
    goal:'A first-time user should discover the parser, inspect a supported sample and get real feedback.',
    target:'https://app.parser.xdr.ooo/',
    max_actions:6,
    allowed_post_paths:['/api/v1/multipass/preview-batch'],
    expected_outcomes:[{id:'discover',expected:'A supported public entry and an understandable log input must be discoverable.'}],
    ...overrides,
  };
}
function pinned(x=source()) {
  const raw=Buffer.from(JSON.stringify(x));
  return {raw,sha:sha256(raw)};
}
function valid(x=source()) {
  const {raw,sha}=pinned(x);
  return validateMission(raw,sha);
}
function observation() {
  return {id:'f'.repeat(64),elements:[
    {ref:'e001',name:'Analyze Logs',kind:'button',enabled:true,visible:true,input_type:''},
    {ref:'e002',name:'Auto Parser raw logs',kind:'textarea',enabled:true,visible:true,input_type:''},
    {ref:'e003',name:'Sign In',kind:'button',enabled:true,visible:true,input_type:''},
    {ref:'e004',name:'Password',kind:'input',enabled:true,visible:true,input_type:'password'},
    {ref:'e005',name:'Unavailable',kind:'button',enabled:false,visible:true,input_type:''},
    {ref:'e006',name:'Choose event',kind:'select',enabled:true,visible:true,input_type:'',
      options:[{value:'0',label:'Event 1',disabled:false},{value:'1',label:'Event 2',disabled:false}]},

  ]};
}
function selected(o,action,why='Independent QA chose this from the current visible browser snapshot.') {
  return {cmd:'act',observation_id:o.id,action,reason:why};
}
function finished(o,override={}) {
  return {cmd:'finish',observation_id:o.id,
    disposition:'NO_CONFIRMED_FINDING',
    rationale:'Only a bounded browser exploration was observed; no release success is inferred.',
    oracle_status:{discover:'OBSERVED'},finding_ids:[],
    ...override,
  };
}
function throws(fn,code) {
  assert.throws(fn,e=>e instanceof ContractError&&e.code===code,code);
}
test('valid pinned synthetic mission and source origin',()=>{
  const m=valid();assert.equal(m.origin,'https://app.parser.xdr.ooo');
  assert.equal(m.max_actions,6);
});
test('externally mismatched frozen mission denied',()=>{
  const {raw}=pinned();throws(()=>validateMission(raw,'0'.repeat(64)),'UNPINNED_REQUIREMENTS');
});
test('unknown mission fields rejected, not executable commands',()=>{
  const x=source();x.command='rm -rf /';
  const p=pinned(x);throws(()=>validateMission(p.raw,p.sha),'BAD_MISSION_SCHEMA');
});
test('disallow arbitrary production or external origin',()=>{
  for(const target of ['https://example.com/','http://app.parser.xdr.ooo/',
      'https://app.parser.xdr.ooo/private','http://169.254.169.254/',
      'http://127.0.0.1:19720/admin','https://user:pass@app.parser.xdr.ooo/']){
    const p=pinned(source({target}));
    assert.throws(()=>validateMission(p.raw,p.sha),ContractError,target);
  }
});
test('untrusted POST endpoints fail mission input contract',()=>{
  for(const allowed_post_paths of [['/api/v1/auth/login'],['/api/v1/parser-pairs/generate'],
    ['/api/v1/delete'],['/*'],['/api/v1/multipass/preview-batch','/api/v1/users']]){
    const p=pinned(source({allowed_post_paths}));
    assert.throws(()=>validateMission(p.raw,p.sha),ContractError);
  }
});
test('no broad action budget and no empty independent oracle',()=>{
  for(const x of [source({max_actions:1000}),source({expected_outcomes:[]}),
    source({expected_outcomes:[{id:'a',expected:'short'}]})]){
    const p=pinned(x);assert.throws(()=>validateMission(p.raw,p.sha),ContractError);
  }
});
test('known same-origin passive requests allowed',()=>{
  const m=valid();assert.equal(allowRequest(m,'https://app.parser.xdr.ooo/main.css','GET').allowed,true);
  assert.equal(allowRequest(m,'https://app.parser.xdr.ooo/robots.txt','HEAD').allowed,true);
});
test('safe compute preview POST permitted only with exact path and no query',()=>{
  const m=valid();
  assert.equal(allowRequest(m,'https://app.parser.xdr.ooo/api/v1/multipass/preview-batch','POST').allowed,true);
  assert.equal(allowRequest(m,'https://app.parser.xdr.ooo/api/v1/multipass/preview-batch?override=true','POST').allowed,false);
  assert.equal(allowRequest(m,'https://app.parser.xdr.ooo/api/v1/parser-pairs/generate','POST').allowed,false);
});
test('block other origin, redirects and mutating verbs',()=>{
  const m=valid();
  for (const [url,verb] of [['https://attacker.example/','GET'],['http://app.parser.xdr.ooo/','GET'],
    ['https://app.parser.xdr.ooo/api/v1/delete','DELETE'],
    ['https://app.parser.xdr.ooo/api/v1/multipass/preview-batch','PATCH'],
    ['https://app.parser.xdr.ooo/api/v1/multipass/preview-batch','PUT']]) {
    assert.equal(allowRequest(m,url,verb).allowed,false);
  }
});
test('current observation and independent reason required before action',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected({...o,id:'0'.repeat(64)},{kind:'click',ref:'e001'}),o,0,m),'STALE_OBSERVATION');
  throws(()=>approveAction(selected(o,{kind:'click',ref:'e001'},'hello'),o,0,m),'MISSING_AI_DECISION_REASON');
});
test('no stale or invented interactive element references',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected(o,{kind:'click',ref:'e999'}),o,0,m),'UNKNOWN_OR_STALE_ELEMENT_REF');
  throws(()=>approveAction(selected(o,{kind:'click',ref:'e005'}),o,0,m),'UNAVAILABLE_ELEMENT');
  throws(()=>approveAction(selected(o,{kind:'click',ref:'body#bad'}),o,0,m),'INVALID_ELEMENT_REF');
});
test('sensitive sign-in and credential entry denied',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected(o,{kind:'click',ref:'e003'}),o,0,m),'SENSITIVE_ACTION_DENIED');
  throws(()=>approveAction(selected(o,{kind:'fill',ref:'e004',text:'p@ss'}),o,0,m),'SENSITIVE_INPUT_DENIED');
});
test('safe exploratory clicks and bounded synthetic log input allowed',()=>{
  const o=observation(),m=valid();
  const a=approveAction(selected(o,{kind:'click',ref:'e001'}),o,1,m);
  assert.equal(a.action.kind,'click');
  const f=approveAction(selected(o,{kind:'fill',ref:'e002',text:'{"action":"blocked"}'}),o,2,m);
  assert.equal(f.action.kind,'fill');
});
test('scripts do not become executable action names',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected(o,{kind:'eval',script:'process.exit(0)'}),o,0,m),'UNKNOWN_ACTION_KIND');
});
test('input bounds and action budget cannot be evaded',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected(o,{kind:'fill',ref:'e002',text:'a'.repeat(9000)}),o,0,m),'UNSAFE_INPUT');
  throws(()=>approveAction(selected(o,{kind:'click',ref:'e001'}),o,6,m),'ACTION_BUDGET_EXCEEDED');
});
test('only safe navigation keys and bounded scroll direction',()=>{
  const o=observation(),m=valid();
  throws(()=>approveAction(selected(o,{kind:'press',ref:'e002',key:'Control+A'}),o,0,m),'UNSAFE_KEY');
  throws(()=>approveAction(selected(o,{kind:'scroll',direction:'right'}),o,0,m),'INVALID_SCROLL_DIRECTION');
  assert.equal(approveAction(selected(o,{kind:'scroll',direction:'down'}),o,0,m).action.kind,'scroll');
});
test('model may select only real currently visible dropdown options',()=>{
  const o=observation(),m=valid();
  const accepted=approveAction(selected(o,{kind:'select',ref:'e006',value:'1'}),o,1,m);
  assert.equal(accepted.action.value,'1');
  throws(()=>approveAction(selected(o,{kind:'select',ref:'e006',value:'999'}),o,0,m),
    'INVALID_SELECT_OPTION');
  throws(()=>approveAction(selected(o,{kind:'select',ref:'e002',value:'1'}),o,0,m),
    'INVALID_SELECT_OPTION');
});
test('viewport intersection prevents distant DOM controls exhausting the list',()=>{
  const before={innerHeight:globalThis.innerHeight,innerWidth:globalThis.innerWidth,
    getComputedStyle:globalThis.getComputedStyle,document:globalThis.document};
  globalThis.innerHeight=800;globalThis.innerWidth=1200;
  globalThis.getComputedStyle=()=>({visibility:'visible',display:'block'});
  const el={tagName:'BUTTON',isContentEditable:false,labels:[],
    innerText:'Back to Review Fields',getAttribute(){return null},
    getBoundingClientRect(){return {width:100,height:30,right:500,left:400,top:400,bottom:430}}};
  globalThis.document={activeElement:el};
  try{
    assert.equal(accessText(el).visible,true);
    assert.equal(accessText(el).focused,true);
    el.getBoundingClientRect=()=>({width:100,height:30,right:500,left:400,top:1800,bottom:1830});
    assert.equal(accessText(el).visible,false);
    el.getBoundingClientRect=()=>({width:100,height:30,right:500,left:400,top:-100,bottom:-70});
    assert.equal(accessText(el).visible,false);
  }finally{
    for(const [k,v] of Object.entries(before)){
      if(v===undefined)delete globalThis[k];else globalThis[k]=v;
    }
  }
});
test('finished exploration rejects any fabricated release PASS',()=>{
  const o=observation();
  for(const x of [
    {disposition:'PASS'}, {oracle_status:{discover:'PASS'}},
    {disposition:'POTENTIAL_FINDING',finding_ids:[]},
    {disposition:'NO_CONFIRMED_FINDING',oracle_status:{discover:'FAIL'}},
  ])assert.throws(()=>approveFinish(finished(o,x),o,2),ContractError);
});
test('finish requires actual selected exploratory action and current observation',()=>{
  const o=observation();
  throws(()=>approveFinish(finished(o),o,0),'NO_MODEL_EXPLORATION');
  throws(()=>approveFinish(finished(o,{observation_id:'0'.repeat(64)}),o,3),'STALE_OBSERVATION');
});
test('HTML select options are visible without leaking credential values',()=>{
  const before={innerHeight:globalThis.innerHeight,innerWidth:globalThis.innerWidth,
    getComputedStyle:globalThis.getComputedStyle,document:globalThis.document};
  globalThis.innerHeight=900;globalThis.innerWidth=1500;
  globalThis.getComputedStyle=()=>({visibility:'visible',display:'block'});
  const el={tagName:'SELECT',isContentEditable:false,labels:[],options:[
    {value:'0',textContent:'Event 1',disabled:false},
    {value:'1',textContent:'Event 2',disabled:false}
  ],innerText:'Choose event',
  getAttribute(k){return k==='aria-label'?'Choose event':null},
  getBoundingClientRect(){return {width:150,height:35,right:700,left:550,top:100,bottom:135}}};
  globalThis.document={activeElement:null};
  try{const meta=accessText(el);assert.equal(meta.kind,'select');assert.equal(meta.options[1].value,'1');}
  finally{for(const [k,v] of Object.entries(before)){
    if(v===undefined)delete globalThis[k];else globalThis[k]=v;
  }}
});
test('a real finding remains a candidate not automatically confirmed',()=>{
  const o=observation();
  const f=approveFinish(finished(o,{disposition:'POTENTIAL_FINDING',
    finding_ids:['UI-COPY-001'],oracle_status:{discover:'FAIL'}}),o,3);
  assert.equal(f.authority,'T0_ADVISORY_ONLY');
  assert.equal(f.full_user_e2e_verified,false);
});
test('observer digest is deterministic and run bound',()=>{
  assert.equal(observationDigest({x:1},'nonce'),observationDigest({x:1},'nonce'));
  assert.notEqual(observationDigest({x:1},'nonce'),observationDigest({x:1},'other'));
});
test('CLI refuses unpinned or overwritten sessions even before browser runs',()=>{
  const opts=['--mission','/tmp/foo','--expected-plan-sha256','a'.repeat(64),
    '--expected-html-sha256','b'.repeat(64),'--out','/tmp/qa-new'];
  const parsed=argvOptions(opts);
  assert.equal(parsed['--mission'],'/tmp/foo');
  assert.throws(()=>argvOptions(opts.slice(0,6)),ContractError);
  assert.throws(()=>argvOptions([...opts.slice(0,6),'--out','relative']),ContractError);
});
