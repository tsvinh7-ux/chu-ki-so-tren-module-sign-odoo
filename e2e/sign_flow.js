// Ký số USB token NGAY TRONG trang ký của module Sign (bản giả lập theo Odoo 17):
//   node e2e/sign_flow.js <sign_urls.json> <thư mục PEM> <agent linux> <thư mục ảnh> [base_url]
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const [,, urlsFile, pemDir, agentBin, outDir, baseArg] = process.argv;
const BASE = baseArg || 'http://localhost:8070';
const urls = JSON.parse(fs.readFileSync(urlsFile, 'utf8').trim().replace(/^E2E_JSON=/, ''));
fs.mkdirSync(outDir, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = (page, name, full) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: !!full });

let agent = null;
async function startAgent(who) {
  if (agent) { agent.kill(); await sleep(800); }
  agent = spawn(agentBin, [], { env: Object.assign({}, process.env, {
    DMAG_AGENT_PEM_CERT: path.join(pemDir, who + '_cert.pem'),
    DMAG_AGENT_PEM_KEY: path.join(pemDir, who + '_key.pem'),
    DMAG_AGENT_CONFIG_DIR: path.join(pemDir, 'agentcfg-sign-' + who),
    DMAG_AGENT_ALLOWED_ORIGINS: BASE,
  }), stdio: 'ignore' });
  await sleep(800);
}

async function signWithUsb(page, url, name, prefix) {
  await page.goto(BASE + url);
  await page.waitForSelector('.o_sign_mock_page img', { timeout: 30000 });
  await shot(page, prefix + '-1-trang-ky', true);
  await page.click('.o_sign_mock_item.o_mine.o_sign_mock_item_signature');
  await page.waitForSelector('.o_dmag_cks_notice', { timeout: 15000 });
  await page.waitForSelector('.o_dmag_cks_cert', { timeout: 15000 });
  await page.click('.o_dmag_cks_cert input');
  await page.waitForSelector('.o_sign_mock_adopt:not([disabled])', { timeout: 15000 });
  await sleep(400);
  await shot(page, prefix + '-2-hop-ky-usb-token');
  await page.click('.o_sign_mock_adopt');
  await page.fill('.o_sign_mock_item.o_mine input.o_sign_mock_text', name);
  await shot(page, prefix + '-3-da-chon-chu-ky', true);
  await page.click('.o_sign_mock_submit');
  await page.waitForSelector('text=/Bạn đã ký xong|Mọi bên đã ký/', { timeout: 30000 });
  await shot(page, prefix + '-4-ky-xong');
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'vi-VN' });
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') console.log('console error:', m.text()); });
  page.on('pageerror', e => console.log('page error:', e.message));

  await startAgent('customer');
  await signWithUsb(page, urls.customer, 'Nguyễn Văn A', '21-khach');
  await startAgent('dmag');
  await signWithUsb(page, urls.dmag, 'Mitchell Admin', '22-dmag');
  // Mở lại: trang hiện bản hoàn tất có 2 chữ ký số
  await page.goto(BASE + urls.customer);
  await page.waitForSelector('.o_sign_mock_page img', { timeout: 30000 });
  await sleep(500);
  await shot(page, '23-ban-hoan-tat', true);
  await browser.close();
  agent.kill();
  console.log('SIGN FLOW OK');
})().catch(async e => { console.error('SIGN FLOW FAIL', e); if (agent) agent.kill(); process.exit(1); });
