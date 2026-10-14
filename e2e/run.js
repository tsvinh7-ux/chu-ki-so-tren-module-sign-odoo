const { chromium } = require('playwright');
const [,, url1, url2] = process.argv;
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await ctx.newPage();
  page.on('console', m => console.log('console:', m.text()));
  // 1. USB token
  await page.goto('http://localhost:8069' + url1);
  await page.waitForSelector('.cks-cert', { timeout: 15000 });
  await page.screenshot({ path: 'shot1_usb_choose.png' });
  await page.click('#cks-usb-sign');
  await page.waitForSelector('text=đã được các bên ký số đầy đủ', { timeout: 30000 });
  await page.screenshot({ path: 'shot2_usb_done.png' });
  console.log('USB OK:', await page.textContent('#cks-status'));
  // 2. App điện thoại (giả lập)
  await page.goto('http://localhost:8069' + url2);
  await page.click('.cks-tab[data-tab="cloud"]');
  await page.fill('#cks-remote-user', '001090012345');
  await page.click('#cks-cloud-start');
  await page.waitForSelector('#cks-cloud-wait', { timeout: 15000 });
  await page.screenshot({ path: 'shot3_cloud_wait.png' });
  await page.waitForSelector('text=đã được các bên ký số đầy đủ', { timeout: 40000 });
  await page.screenshot({ path: 'shot4_cloud_done.png' });
  console.log('CLOUD OK:', await page.textContent('#cks-status'));
  // mobile view
  const m = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const mp = await m.newPage();
  await mp.goto('http://localhost:8069' + url2);
  await mp.screenshot({ path: 'shot5_mobile.png', fullPage: true });
  await browser.close();
})().catch(e => { console.error('E2E FAIL', e); process.exit(1); });
