#!/usr/bin/env node
'use strict';
/* Nonproduction DataRelay Grant login discovery: real Chromium, GET-only, no credentials. */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function parse(argv) {
  const obj = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--url', '--out', '--expected-html-sha256'].includes(argv[i]) || !argv[i + 1]) {
      throw new Error('INVALID_ARGS');
    }
    obj[argv[i].slice(2)] = argv[i + 1];
  }
  const url = new URL(obj.url);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || !/^[0-9a-f]{64}$/.test(obj['expected-html-sha256'] || '')
      || !path.isAbsolute(obj.out || '')) throw new Error('UNPINNED_OR_UNSAFE_TARGET');
  if (fs.existsSync(obj.out)) throw new Error('REFUSE_OVERWRITE_EVIDENCE');
  return { origin: url.origin, href: url.href, out: obj.out, pin: obj['expected-html-sha256'] };
}
async function main() {
  const opt = parse(process.argv.slice(2));
  const {chromium} = require(
    require.resolve('playwright', {paths: [process.env.PLAYWRIGHT_MODULE_ROOT || process.cwd()]})
  );
  fs.mkdirSync(opt.out, {mode: 0o700});
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({acceptDownloads: false, permissions: []});
  const page = await context.newPage();
  const disallowed = [];
  const browserErrors = [];
  const responses = [];
  await page.route('**/*', async route => {
    const r = route.request();
    let parsed;
    try { parsed = new URL(r.url()); } catch { disallowed.push('BAD_URL'); return route.abort(); }
    if (parsed.origin !== opt.origin || !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) {
      disallowed.push(r.method() + ' ' + parsed.origin);
      return route.abort();
    }
    return route.continue();
  });
  page.on('pageerror', e => browserErrors.push(String(e.message).slice(0, 160)));
  page.on('response', r => responses.push({method:r.request().method(),path:new URL(r.url()).pathname,status:r.status()}));
  await context.tracing.start({screenshots: true, snapshots: true, sources: false});
  let status = 'BLOCKED_EVIDENCE';
  let htmlDigest = '';
  try {
    const response = await page.goto(opt.href, {waitUntil: 'domcontentloaded', timeout: 20000});
    await page.waitForTimeout(750);
    const actualHtml = await response.body();
    htmlDigest = sha(actualHtml);
    const username = await page.locator('input:not([type="password"])').count();
    const password = await page.locator('input[type="password"]').count();
    const signIn = await page.getByRole('button', {name:/Sign In/i}).count();
    const text = await page.locator('body').innerText();
    const contract = response.status() === 200
      && htmlDigest === opt.pin && await page.title() === 'DataRelay Grant'
      && username >= 1 && password >= 1 && signIn >= 1
      && text.includes('Please sign in to continue.')
      && text.includes('Self-service registration is not available.')
      && disallowed.length === 0 && browserErrors.length === 0;
    status = contract ? 'PASS_READONLY_LOGIN_DISCOVERY_ONLY' : 'BLOCKED_EVIDENCE';
    await page.screenshot({path:path.join(opt.out, 'login.png'), fullPage:true});
    const result = {
      kind: 'grant-public-login-readonly-pilot',
      authority: 'T0_ADVISORY_ONLY',
      product_release_gate: false,
      human_equivalent_full_user_e2e: false,
      source_runtime_exact_head_qualified: false,
      browser: 'REAL_CHROMIUM',
      target_origin: opt.origin,
      status,
      html_sha256: htmlDigest,
      expected_html_sha256: opt.pin,
      root_http_status: response.status(),
      username_field_count: username,
      password_field_count: password,
      sign_in_button_count: signIn,
      blocked_nonread_requests: disallowed,
      browser_errors: browserErrors,
      network: responses,
      screenshot_sha256: sha(fs.readFileSync(path.join(opt.out, 'login.png'))),
    };
    fs.writeFileSync(path.join(opt.out, 'result.json'), JSON.stringify(result, null, 2) + '\n', {mode:0o600});
    console.log(JSON.stringify({status,html_sha256:htmlDigest,network_requests:responses.length,
      blocked_nonread_requests:disallowed.length,authority:'T0_ADVISORY_ONLY'}));
    return contract ? 0 : 2;
  } finally {
    await context.tracing.stop({path: path.join(opt.out, 'trace.zip')});
    await browser.close();
  }
}
if (require.main === module) {
  main().then(code => {process.exitCode=code}).catch(e => {
    console.error('PILOT_BLOCK ' + e.message);
    process.exitCode=3;
  });
}
module.exports = {parse};
