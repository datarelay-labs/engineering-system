import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
test('preserve value',async ({app,browser})=>{
 await app.open(process.env.POC_FAULT==='1'?'/fault':'/');
 await browser.locator('#value').fill('sentinel-20261125');
 await browser.locator('button').click();
 await expect(browser.locator('#result')).toHaveText('sentinel-20261125');
});
