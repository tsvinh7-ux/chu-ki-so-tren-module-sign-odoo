const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 950 } })).newPage();
  await page.goto('http://localhost:8069/web/login');
  await page.fill('input[name=login]', 'admin'); await page.fill('input[name=password]', 'admin');
  await page.click('button[type=submit]'); await page.waitForSelector('.o_main_navbar');
  await page.goto('http://localhost:8069/web#model=dmag.cks.envelope&id=1&view_type=form');
  await page.waitForSelector('.o_form_view'); await page.waitForTimeout(1500);
  await page.click('a[name="verify"]').catch(() => {}); await page.waitForTimeout(800);
  await page.screenshot({ path: 'shot6_backend_envelope.png', fullPage: true });
  await page.goto('http://localhost:8069/web#action=dmag_esign_cks.action_cks_envelope&view_type=list');
  await page.waitForTimeout(1500);
  await page.click('.o_facet_remove').catch(() => {}); await page.waitForTimeout(1000);
  await page.screenshot({ path: 'shot7_backend_list.png' });
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
