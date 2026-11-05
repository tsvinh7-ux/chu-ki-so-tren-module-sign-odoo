// Ký số NGAY TRONG trang ký của module Sign (bản giả lập theo Odoo 17), qua module dmag_esign_cks_sign:
//   khách: tab "USB token" (phần mềm DMAG Ký số, chế độ PEM) · DMAG: tab "App ký số" (Viettel MySign giả lập)
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

async function openAndAdopt(page, url, prefix) {
  await page.goto(BASE + url);
  await page.waitForSelector('.o_sign_mock_page img', { timeout: 30000 });
  await shot(page, prefix + '-1-trang-ky', true);
  await page.click('.o_sign_mock_item.o_mine.o_sign_mock_item_signature');
  await page.waitForSelector('.o_dmag_cks_notice', { timeout: 15000 });
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'vi-VN' });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('page error:', e.message));

  // 1. Khách: USB token
  await startAgent('customer');
  await openAndAdopt(page, urls.customer, '31-khach');
  await page.waitForSelector('.o_dmag_cks_cert', { timeout: 15000 });
  await page.click('.o_dmag_cks_cert input');
  await page.waitForSelector('.o_sign_mock_adopt:not([disabled])', { timeout: 15000 });
  await sleep(300);
  await shot(page, '31-khach-2-hop-ky-usb-token');
  await page.click('.o_sign_mock_adopt');
  await page.fill('.o_sign_mock_item.o_mine input.o_sign_mock_text', 'Nguyễn Văn A');
  await page.click('.o_sign_mock_submit');
  await page.waitForSelector('#dmag_cks_overlay', { timeout: 15000 });
  await shot(page, '31-khach-3-dang-ky-so');
  await page.waitForSelector('text=Bạn đã ký xong', { timeout: 30000 });
  await shot(page, '31-khach-4-ky-xong');

  // 2. DMAG: App ký số (Viettel MySign)
  await openAndAdopt(page, urls.dmag, '32-dmag');
  await page.click('.o_dmag_cks_cloud_button');
  await page.waitForSelector('.o_dmag_cks_account', { timeout: 15000 });
  await page.click('.o_dmag_cks_check');
  await page.waitForSelector('text=Chứng thư:', { timeout: 20000 });
  await page.waitForSelector('.o_sign_mock_adopt:not([disabled])', { timeout: 15000 });
  await sleep(300);
  await shot(page, '32-dmag-2-hop-ky-mysign');
  await page.click('.o_sign_mock_adopt');
  await page.fill('.o_sign_mock_item.o_mine input.o_sign_mock_text', 'Giám đốc DMAG');
  await page.click('.o_sign_mock_submit');
  await page.waitForSelector('text=/Mời mở ứng dụng/', { timeout: 20000 });
  await shot(page, '32-dmag-3-cho-xac-nhan-app');
  await page.waitForSelector('text=Mọi bên đã ký', { timeout: 60000 });
  await shot(page, '32-dmag-4-hoan-tat');

  await page.goto(BASE + urls.customer);
  await page.waitForSelector('.o_sign_mock_page img', { timeout: 30000 });
  await sleep(500);
  await shot(page, '33-ban-hoan-tat', true);
  await browser.close();
  agent.kill();
  console.log('SIGN FLOW OK');
})().catch(async e => { console.error('SIGN FLOW FAIL', e); if (agent) agent.kill(); process.exit(1); });
