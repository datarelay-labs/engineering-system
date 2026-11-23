#!/usr/bin/env node
'use strict';
/**
 * Explicit, dev-only Chrome DevTools MCP observation (never a user E2E gate).
 * No install, user session attachment, credentials or implicit authority.
 */
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const {spawn,spawnSync}=require('node:child_process');
const contract=require('./readonly-contract.cjs');

function fail(code){throw new Error(code);}
function argsFrom(argv){
  const allowed=new Set(['--target','--mcp-binary','--chrome-binary','--out']);
  const result={trace:false};
  for(let i=0;i<argv.length;i++){
    if(argv[i]==='--trace'){
      if(result.trace)fail('DUPLICATE_TRACE');
      result.trace=true;continue;
    }
    const key=argv[i],val=argv[++i];
    if(!allowed.has(key)||!val||result[key])fail('INVALID_ARGUMENTS');
    result[key]=val;
  }
  for(const key of allowed) if(!result[key])fail('MISSING_ARGUMENT_'+key);
  return result;
}
function checkBinary(candidate,expectedPackage=null){
  if(typeof candidate!=='string'||!path.isAbsolute(candidate))fail('ABSOLUTE_BINARY_REQUIRED');
  const real=fs.realpathSync(candidate);
  const stat=fs.statSync(real);
  if(!stat.isFile() || !(stat.mode & 0o111))fail('NOT_EXECUTABLE');
  if(expectedPackage){
    let dir=path.dirname(real),verified=false;
    for(let i=0;i<8;i++){
      const p=path.join(dir,'package.json');
      if(fs.existsSync(p)){
        let meta;try{meta=JSON.parse(fs.readFileSync(p,'utf8'));}catch{fail('PACKAGE_METADATA_INVALID');}
        if(meta.name===expectedPackage && meta.version==='1.12.0'){verified=true;break;}
      }
      const parent=path.dirname(dir);
      if(parent===dir)break;
      dir=parent;
    }
    if(!verified)fail('UNPINNED_CHROME_DEVTOOLS_MCP_PACKAGE');
  }
  return real;
}
function requireSupportedRuntime(chromeBinary){
  const [major,minor]=process.versions.node.split('.').map(Number);
  if (!((major===20 && minor>=19) || (major===22 && minor>=12) || major>=23)) {
    fail('NODE_RUNTIME_UNSUPPORTED');
  }
  // Chrome's URL-pattern network guard requires Chrome 149 or later.
  const p=spawnSync(chromeBinary,['--version'],{encoding:'utf8',timeout:4500});
  const version=/Chrome(?: for Testing)?\s+(\d+)\./.exec(p.stdout||'');
  if(p.status!==0 || !version || Number(version[1])<149)fail('CHROME_URL_GUARD_UNSUPPORTED');
}

function outputDirectory(raw){
  if(!path.isAbsolute(raw) || path.dirname(path.resolve(raw))!==fs.realpathSync(os.tmpdir()) ||
     !/^es-devtools-[a-zA-Z0-9_-]{4,80}$/.test(path.basename(raw)))fail('OUTPUT_NOT_ISOLATED_TMP');
  if(fs.existsSync(raw))fail('REFUSE_EXISTING_EVIDENCE');
  fs.mkdirSync(raw,{mode:0o700});
  fs.chmodSync(raw,0o700);
  return raw;
}
function contents(r){return(r?.content||[]).filter(x=>x?.type==='text').map(x=>x.text).join('\n');}
function rpcClient(server){
  let buffer='',nextId=0,ended=false;
  const pending=new Map();
  server.stdout.setEncoding('utf8');
  server.stdout.on('data',chunk=>{
    buffer+=chunk;
    if(buffer.length>2*1024*1024){buffer='';return;}
    const messages=buffer.split('\n');buffer=messages.pop();
    for(const line of messages){
      let item;try{item=JSON.parse(line);}catch{continue;}
      if(!Number.isInteger(item.id))continue;
      const job=pending.get(item.id);
      if(!job)continue;
      clearTimeout(job.timer);pending.delete(item.id);
      if(item.error)job.reject(new Error('MCP_RPC_ERROR'));
      else job.resolve(item.result);
    }
  });
  function cancel(reason){
    if(ended)return;ended=true;
    for(const job of pending.values()){clearTimeout(job.timer);job.reject(new Error(reason));}
    pending.clear();
  }
  server.on('close',()=>cancel('MCP_SESSION_CLOSED'));
  server.on('error',()=>cancel('MCP_SPAWN_FAILED'));
  function call(method,params={},timeoutMs=42000){
    if(ended)fail('MCP_SESSION_CLOSED');
    return new Promise((resolve,reject)=>{
      const id=++nextId;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error('MCP_TIMEOUT'));},timeoutMs);
      pending.set(id,{resolve,reject,timer});
      try{
        server.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
      }catch(_){pending.delete(id);clearTimeout(timer);reject(new Error('MCP_WRITE_FAILED'));}
    });
  }
  function notify(method){server.stdin.write(JSON.stringify({jsonrpc:'2.0',method})+'\n');}
  async function tool(name,parameters={},timeout=42000){
    const resp=await call('tools/call',{name,arguments:parameters},timeout);
    if(resp?.isError)fail('MCP_TOOL_FAILED_'+name);
    const text=contents(resp);
    if(Buffer.byteLength(text)>256*1024)fail('UNBOUNDED_TOOL_OUTPUT');
    return text;
  }
  return{call,notify,tool,cancel};
}
async function main(){
  const parsed=argsFrom(process.argv.slice(2));
  const target=contract.validateTarget(parsed['--target']);
  const mcpBinary=checkBinary(parsed['--mcp-binary'],'chrome-devtools-mcp');
  const chromeBinary=checkBinary(parsed['--chrome-binary']);
  requireSupportedRuntime(chromeBinary);
  const out=outputDirectory(parsed['--out']);
  let server,client;
  try{
    const flags=contract.serverFlags(target,chromeBinary);
    server=spawn(mcpBinary,flags,{
      cwd:out,env:{
        ...process.env,
        CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS:'1',
        CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY:'1',
        CI:'1',NO_COLOR:'1'
      },
      stdio:['pipe','pipe','pipe'],
    });
    // Never persist MCP stderr, browser storage or raw tool bodies.
    server.stderr.on('data',()=>{});
    client=rpcClient(server);
    const initialized=await client.call('initialize',{
      protocolVersion:'2025-06-18',
      capabilities:{},
      clientInfo:{name:'engineering-system-local-readonly',version:'1.0.0'},
    },22000);
    if(initialized?.protocolVersion!=='2025-06-18')fail('UNEXPECTED_MCP_PROTOCOL');
    client.notify('notifications/initialized');
    const listing=await client.call('tools/list',{});
    const names=new Set((listing?.tools||[]).map(x=>x.name));
    for(const required of ['new_page','list_pages','list_console_messages','list_network_requests']){
      if(!names.has(required))fail('REQUIRED_MCP_TOOL_UNAVAILABLE');
    }
    if(parsed.trace&&!names.has('performance_start_trace'))fail('PERFORMANCE_TOOL_UNAVAILABLE');
    const opened=await client.tool('new_page',{url:target,timeout:20000},35000);
    const pages=await client.tool('list_pages',{});
    const found=opened.match(/(\d+):.*127\.0\.0\.1/) || pages.match(/(\d+):.*127\.0\.0\.1/);
    if(!found)fail('TARGET_PAGE_NOT_OBSERVED');
    const pageId=Number(found[1]);
    const consoleText=await client.tool('list_console_messages',{pageId,includeStackTraces:true,pageSize:50});
    const networkText=await client.tool('list_network_requests',{pageId,pageSize:50});
    let traceSha256=null,traceBytes=null,insights=false;
    if(parsed.trace){
      const tracePath=path.join(out,'local-performance-trace.json.gz');
      const response=await client.tool('performance_start_trace',{
        pageId,autoStop:true,reload:true,ignoreCache:true,filePath:tracePath
      },60000);
      if(!fs.existsSync(tracePath))fail('TRACE_NOT_SAVED');
      const stat=fs.statSync(tracePath);
      if(!stat.isFile()||stat.size>32*1024*1024)fail('TRACE_UNBOUNDED');
      fs.chmodSync(tracePath,0o600);
      traceBytes=stat.size;
      traceSha256=contract.digest(fs.readFileSync(tracePath));
      insights=/insights|insight|long task|LCP|TBT|documentlatency/i.test(response);
    }
    const report=contract.summarize({target,consoleText,networkText,
      traceSha256,traceBytes,insights});
    report.mcp_package_version='1.12.0';
    report.mcp_protocol=initialized.protocolVersion;
    report.captured_at_utc=new Date().toISOString();
    report.privacy='NO_AUTHENTICATED_SESSION_OR_RAW_MESSAGES';
    report.browser_isolation='TEMPORARY_USER_DATA_DIR';
    const receipt=path.join(out,'diagnostics.json');
    fs.writeFileSync(receipt,JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});
    console.log(JSON.stringify({
      status:'CAPTURED',
      receipt,console_errors:report.console.error_count,
      source_bound_errors:report.console.source_bound_count,
      http_failures:report.network.failure_count,
      performance_trace:traceSha256!==null,
      release_authority:'NO'
    }));
  }finally{
    if(client)client.cancel('DISPOSING');
    if(server){
      try{server.stdin.end();server.kill('SIGTERM');}catch(_){}
      await new Promise(resolve=>{
        if(server.exitCode!==null||server.signalCode!==null)return resolve();
        const timeout=setTimeout(()=>{server.kill('SIGKILL');resolve();},3500);
        server.once('close',()=>{clearTimeout(timeout);resolve();});
      });
    }
  }
}
main().catch(e=>{
  // Reason code only. Raw browser/MCP output could contain sensitive values.
  console.error('DEVTOOLS_DIAGNOSTIC=BLOCK reason='+String(e.message).replace(/[^A-Za-z0-9_]/g,'_').slice(0,110));
  process.exitCode=3;
});
