'use strict';
/** Pure, provider-independent redaction and host-contract rules for DevTools MCP. */
const crypto = require('node:crypto');

const MAX_INPUT_BYTES = 256 * 1024;
const MAX_RECORDS = 50;
const SCHEMA = 'engineering.chrome-devtools-readonly.v1';
function digest(text) { return crypto.createHash('sha256').update(text).digest('hex'); }

function validateTarget(raw) {
  if (typeof raw !== 'string' || raw.length > 170) throw new Error('INVALID_TARGET');
  let target;
  try { target = new URL(raw); }
  catch (_) { throw new Error('INVALID_TARGET'); }
  if (target.protocol !== 'http:' ||
      !['127.0.0.1','localhost'].includes(target.hostname) ||
      !target.port || Number(target.port) < 1024 ||
      target.pathname !== '/' || target.search || target.hash ||
      target.username || target.password) {
    throw new Error('NONPRODUCTION_LOOPBACK_ONLY');
  }
  return target.href;
}

function serverFlags(target, chromeBin) {
  const url = validateTarget(target);
  if (typeof chromeBin !== 'string' || !chromeBin.startsWith('/')) {
    throw new Error('ABSOLUTE_CHROME_BIN_REQUIRED');
  }
  return [
    '--headless=true', '--isolated=true', '--executablePath=' + chromeBin,
    '--no-usage-statistics', '--no-performance-crux',
    '--no-javascript-evaluation', '--no-file-navigations',
    '--categoryInput=false', '--categoryNetworkMocks=false',
    '--categoryExperimentalThirdParty=false', '--categoryExtensions=false',
    '--categoryExperimentalWebmcp=false', '--categoryPwa=false',
    '--categoryWebauthn=false', '--categoryMemory=false',
    '--redactNetworkHeaders=true', '--maxNavigationSaved=1',
    '--allowedUrlPattern=' + url + '*',
  ];
}

function checkedText(raw) {
  if (typeof raw !== 'string' || Buffer.byteLength(raw,'utf8') > MAX_INPUT_BYTES) {
    throw new Error('UNBOUNDED_TOOL_OUTPUT');
  }
  return raw;
}

function parseConsole(raw) {
  const lines=checkedText(raw).split('\n');
  const messages=[];
  for (const line of lines) {
    const match=/^msgid=(\d+)\s+\[([a-z]+)\]\s+(.+)$/.exec(line);
    if (match && messages.length < MAX_RECORDS) {
      if (match[2] !== 'error' && match[2] !== 'warning') continue;
      messages.push({
        kind:match[2],
        message_sha256:digest(match[3]),
        source:null,
      });
      continue;
    }
    if (!messages.length || messages[messages.length-1].source) continue;
    const site=/^\s+at\s+.*?\(?([^()\s]+):(\d+):(\d+)\)?\s*$/.exec(line);
    if (!site) continue;
    const basename=site[1].split('?')[0].split('/').pop();
    const file=basename && /^[a-zA-Z0-9_.-]{1,96}$/.test(basename) ? basename : null;
    const lineNumber=Number(site[2]),columnNumber=Number(site[3]);
    if (file && lineNumber>0 && lineNumber<1000000 && columnNumber>0 && columnNumber<1000000){
      messages[messages.length-1].source={file,line:lineNumber,column:columnNumber};
    }
  }
  return {
    error_count:messages.filter(m=>m.kind==='error').length,
    warning_count:messages.filter(m=>m.kind==='warning').length,
    source_bound_count:messages.filter(m=>!!m.source).length,
    entries:messages,
    truncated:messages.length >= MAX_RECORDS,
  };
}

function parseNetwork(raw, targetRaw) {
  const target=new URL(validateTarget(targetRaw));
  const lines=checkedText(raw).split('\n');
  const records=[];
  for (const line of lines) {
    const match=/^reqid=(\d+)\s+([A-Z]+)\s+(\S+)\s+\[(\d{3})\]/.exec(line);
    if(!match || records.length>=MAX_RECORDS)continue;
    let req;
    try { req=new URL(match[3]); }
    catch (_) { continue; }
    if(req.origin!==target.origin)throw new Error('CROSS_ORIGIN_TOOL_DATA');
    // Never copy URL path, query, response body or headers into agent evidence.
    const status=Number(match[4]);
    records.push({
      method:match[2],status,
      route_sha256:digest(req.pathname),
    });
  }
  return {
    request_count:records.length,
    failure_count:records.filter(r=>r.status>=400).length,
    statuses:records.reduce((o,r)=>(o[String(r.status)]=(o[String(r.status)]||0)+1,o),{}),
    entries:records,
    truncated:records.length>=MAX_RECORDS,
  };
}

function summarize({target,consoleText,networkText,traceSha256=null,traceBytes=null,insights=false}) {
  const u=new URL(validateTarget(target));
  return {
    schema:SCHEMA,
    execution:'NONPRODUCTION_LOOPBACK_OBSERVATION',
    authority:'T0_ADVISORY_ONLY',
    full_user_e2e:'NOT_ESTABLISHED',
    release_authority:'NO',
    raw_page_data_stored:false,
    origin:u.origin,
    console:parseConsole(consoleText),
    network:parseNetwork(networkText,target),
    performance:{
      trace_sha256:traceSha256,
      trace_bytes:traceBytes,
      insight_available:insights===true,
    },
  };
}
module.exports={SCHEMA,digest,validateTarget,serverFlags,parseConsole,parseNetwork,summarize};
