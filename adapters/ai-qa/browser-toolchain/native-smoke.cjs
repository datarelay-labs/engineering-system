const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');
const net = require('node:net');

async function main() {
  const reservation = net.createServer();
  await new Promise((resolve, reject) => {
    reservation.once('error', reject);
    reservation.listen(0, '127.0.0.1', resolve);
  });
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const origin = 'http://127.0.0.1:' + port;
  const fixture = spawn(process.execPath, ['fixture.cjs', String(port)], {stdio: 'ignore'});
  let browser;
  try {
    for (let i = 0; i < 50; i++) {
      try { if ((await fetch(origin)).ok) break; } catch {}
      if (i === 49) throw Error('fixture not ready');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({headless:true, chromiumSandbox:true});
    const page = await browser.newPage();
    for (const [path, expected] of [['/', 'sentinel-20261125'], ['/fault', 'WRONG']]) {
      await page.goto(origin + path);
      await page.locator('#value').fill('sentinel-20261125');
      await page.locator('button').click();
      const observed = await page.locator('#result').textContent();
      assert.equal(observed, expected);
      if (path === '/fault') assert.notEqual(observed, 'sentinel-20261125');
      console.log(path, observed, path === '/fault' ? 'FAULT_DETECTED' : 'NORMAL_PASS');
    }
  } finally {
    if (browser) await browser.close();
    fixture.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
