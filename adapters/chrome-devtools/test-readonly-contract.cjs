'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const contract=require('./readonly-contract.cjs');

const TARGET='http://127.0.0.1:44321/';
test('only isolated nonproduction loopback origins with no credentials or query',()=>{
  for(const value of [
    'https://app.parser.xdr.ooo/','http://evil.example/','http://127.0.0.1/',
    'http://127.0.0.1:80/','http://localhost:44321/private',
    'http://user:pass@127.0.0.1:44321/',
    TARGET+'?token=secret',TARGET+'#frag','file:///tmp/secret',
    'http://127.0.0.1:44321.evil.example/',
  ]) assert.throws(()=>contract.validateTarget(value));
  assert.equal(contract.validateTarget(TARGET),TARGET);
  assert.equal(contract.validateTarget('http://localhost:44321/'),'http://localhost:44321/');
});
test('disallowed tool capabilities and third-party transmissions are removed',()=>{
  const args=contract.serverFlags(TARGET,'/opt/chrome/chrome');
  for(const flag of [
    '--headless=true','--isolated=true','--no-usage-statistics',
    '--no-performance-crux','--no-javascript-evaluation',
    '--no-file-navigations','--categoryInput=false',
    '--categoryNetworkMocks=false','--categoryExperimentalThirdParty=false',
    '--allowedUrlPattern='+TARGET+'*',
  ]) assert.ok(args.includes(flag),flag);
  assert.ok(!args.some(x=>x.includes('allowUnrestrictedPaths')));
  assert.throws(()=>contract.serverFlags(TARGET,'chrome'));
});
test('console and network are anonymized but preserve source location and status',()=>{
  const secret='SENSITIVE_OBSERVATION_TOKEN_ABC123';
  const consoles='## Console messages\n'+
   'msgid=1 [error] '+secret+' actual exception body\n'+
   '  at  (boom.js:1:9)\n'+
   'msgid=2 [error] Uncaught Error: forbidden access\n'+
   '  at  (src/client.ts:42:7)\n'+
   'msgid=3 [warning] unrelated header \n';
  const network='## Network requests\n'+
    'reqid=1 GET '+TARGET+' [200]\n'+
    'reqid=2 GET '+TARGET+'fault?apikey='+secret+' [503]\n'+
    'reqid=3 GET '+TARGET+'done [204]\n';
  const report=contract.summarize({target:TARGET,consoleText:consoles,networkText:network,
    traceSha256:'a'.repeat(64),traceBytes:120000,insights:true});
  assert.equal(report.console.error_count,2);
  assert.equal(report.console.warning_count,1);
  assert.equal(report.console.source_bound_count,2);
  assert.deepEqual(report.console.entries[0].source,{file:'boom.js',line:1,column:9});
  assert.deepEqual(report.console.entries[1].source,{file:'client.ts',line:42,column:7});
  assert.equal(report.network.request_count,3);
  assert.equal(report.network.failure_count,1);
  assert.equal(report.network.statuses['503'],1);
  assert.equal(report.full_user_e2e,'NOT_ESTABLISHED');
  assert.equal(report.authority,'T0_ADVISORY_ONLY');
  assert.equal(report.raw_page_data_stored,false);
  assert.equal(report.release_authority,'NO');
  const serialized=JSON.stringify(report);
  assert.ok(!serialized.includes(secret));
  assert.ok(!serialized.includes('apikey'));
  assert.ok(!serialized.includes('forbidden'));
  assert.ok(!serialized.includes('fault'));
  assert.ok(!serialized.includes('reqid'));
  assert.ok(!serialized.includes('http://127.0.0.1:44321/fault'));
  assert.equal(report.performance.trace_sha256,'a'.repeat(64));
});
test('malformed, oversized or cross-origin tool output fails closed',()=>{
  assert.throws(()=>contract.parseConsole('x'.repeat(256*1024+1)),/UNBOUNDED/);
  assert.throws(()=>contract.parseNetwork(
    'reqid=1 GET https://evil.example/token [200]',TARGET
  ),/CROSS_ORIGIN/);
  assert.throws(()=>contract.parseNetwork(123,TARGET),/UNBOUNDED/);
  const sample='msgid=1 [error] sensitive failure\n  at (http://evil.example/a.js?secret=x:50:6)\n';
  const output=contract.parseConsole(sample);
  assert.equal(output.error_count,1);
  assert.ok(output.entries[0].source===null || output.entries[0].source.file==='a.js');
  assert.ok(!JSON.stringify(output).includes('secret=x'));
});

test('actual CLI rejects nonlocal target before MCP launch or evidence writes',()=>{
  const {spawnSync}=require('node:child_process');
  const os=require('node:os');
  const path=require('node:path');
  const fs=require('node:fs');
  const output=path.join(os.tmpdir(),'es-devtools-inval-'+process.pid+'-'+Date.now());
  const p=spawnSync(process.execPath,[
    path.join(__dirname,'readonly-probe.cjs'),
    '--target','https://evil.example/',
    '--mcp-binary','/does-not-exist/mcp',
    '--chrome-binary','/does-not-exist/chrome',
    '--out',output,
  ],{encoding:'utf8',timeout:5000});
  assert.equal(p.status,3);
  assert.match(p.stderr,/NONPRODUCTION_LOOPBACK_ONLY/);
  assert.ok(!fs.existsSync(output));
});
