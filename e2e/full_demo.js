// Chạy thử toàn bộ luồng và chụp màn hình:
//   node e2e/full_demo.js <urls.json> <thư mục chứng thư PEM> <file agent linux> <thư mục ảnh>
// urls.json là dòng E2E_JSON=... do e2e/odoo_shell_setup.py in ra.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const [,, urlsFile, pemDir, agentBin, outDir] = process.argv;
const BASE = process.env.ODOO_URL || 'http://localhost:8069';
const envelopes = JSON.parse(fs.readFileSync(urlsFile, 'utf8').trim().replace(/^E2E_JSON=/, ''));
fs.mkdirSync(outDir, { recursive: true });
const shot = (page, name, full) => page.screenshot({ path: path.join(outDir, name + '.png'), fullPage: !!full });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let agent = null;
async function startAgent(who) {
  if (agent) { agent.kill(); await sleep(800); }
  agent = spawn(agentBin, [], { env: Object.assign({}, process.env, {
    DMAG_AGENT_PEM_CERT: path.join(pemDir, who + '_cert.pem'),
    DMAG_AGENT_PEM_KEY: path.join(pemDir, who + '_key.pem'),
    DMAG_AGENT_CONFIG_DIR: path.join(pemDir, 'agentcfg-' + who),
  }), stdio: 'ignore' });
  await sleep(800);
}

async function login(ctx) {
  const page = await ctx.newPage();
  await page.goto(BASE + '/web/login');
  await page.fill('input[name=login]', 'admin');
  await page.fill('input[name=password]', 'admin');
  await page.click('button[type=submit]');
  await page.waitForSelector('.o_main_navbar');
  return page;
}

async function openBackend(page, hash) {
  await page.goto(BASE + '/web#' + hash);
  await page.reload();  // cùng URL hash thì Odoo không tải lại dữ liệu
  await page.waitForSelector('.o_action_manager .o_view_controller', { timeout: 20000 });
  await sleep(1200);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'vi-VN' });
  const admin = await login(ctx);
  const org = envelopes.find(e => e.kind === 'organization');
  const ind = envelopes.find(e => e.kind === 'individual');

  // 01. Danh sách hồ sơ ký số
  await openBackend(admin, 'action=dmag_esign_cks.action_cks_envelope&view_type=list');
  await shot(admin, '01-danh-sach-ho-so');
  // 02. Gửi PDF đi ký số
  await openBackend(admin, 'action=dmag_esign_cks.action_cks_send_wizard');
  await shot(admin, '02-gui-pdf-di-ky');

  // 03-04. Khách ký bằng USB token
  await startAgent('customer');
  const cust = await ctx.newPage();
  await cust.goto(BASE + org.url);
  await cust.waitForSelector('.cks-cert', { timeout: 20000 });
  await shot(cust, '03-khach-chon-usb-token');
  await cust.click('#cks-usb-sign');
  await cust.waitForSelector('text=đang chờ bên phát hành ký số', { timeout: 30000 });
  await shot(cust, '04-khach-ky-xong-cho-dmag');

  // 05. Quản trị: đến lượt DMAG
  await openBackend(admin, 'model=dmag.cks.envelope&id=' + org.id + '&view_type=form');
  await shot(admin, '05-den-luot-dmag-ky');

  // 06-07. DMAG ký bằng USB token chứng thư tổ chức
  await startAgent('dmag');
  const dmag = await ctx.newPage();
  await dmag.goto(BASE + org.company_url);
  await dmag.waitForSelector('.cks-cert', { timeout: 20000 });
  await shot(dmag, '06-dmag-ky-usb-token');
  await dmag.click('#cks-usb-sign');
  await dmag.waitForSelector('text=đã được các bên ký số đầy đủ', { timeout: 30000 });
  await shot(dmag, '07-hoan-tat');

  // 08. Quản trị: kết quả kiểm tra chữ ký
  await openBackend(admin, 'model=dmag.cks.envelope&id=' + org.id + '&view_type=form');
  await admin.click('a[name="verify"]').catch(() => {});
  await sleep(800);
  await shot(admin, '08-kiem-tra-chu-ky', true);

  // 09-10. Hồ sơ cá nhân: khách ký bằng app (giả lập), sau đó DMAG ký USB
  const app = await ctx.newPage();
  await app.goto(BASE + ind.url);
  await app.click('.cks-tab[data-tab="cloud"]');
  await app.fill('#cks-remote-user', '001090012345');
  await app.click('#cks-cloud-start');
  await app.waitForSelector('#cks-cloud-wait', { timeout: 20000 });
  await shot(app, '09-khach-ky-bang-app');
  await app.waitForSelector('text=đang chờ bên phát hành ký số', { timeout: 40000 });
  const dmag2 = await ctx.newPage();
  await dmag2.goto(BASE + ind.company_url);
  await dmag2.waitForSelector('.cks-cert', { timeout: 20000 });
  await dmag2.click('#cks-usb-sign');
  await dmag2.waitForSelector('text=đã được các bên ký số đầy đủ', { timeout: 30000 });

  // 10. Danh sách sau khi ký xong
  await openBackend(admin, 'action=dmag_esign_cks.action_cks_envelope&view_type=list');
  await admin.click('.o_facet_remove').catch(() => {});
  await sleep(1000);
  await shot(admin, '10-danh-sach-da-ky');

  // 11. Cấu hình
  await openBackend(admin, 'action=dmag_esign_cks.action_cks_provider&view_type=list');
  await shot(admin, '11-cau-hinh-nha-cung-cap');
  await openBackend(admin, 'action=dmag_esign_cks.action_cks_sign_link&view_type=list');
  await admin.click('.o_data_row td.o_data_cell');
  await sleep(1200);
  await shot(admin, '12-ket-noi-module-sign');

  // 13. Điện thoại
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const mp = await mobile.newPage();
  await mp.goto(BASE + ind.url);
  await sleep(800);
  await shot(mp, '13-dien-thoai', true);

  await browser.close();
  if (agent) agent.kill();
  console.log('DEMO OK');
})().catch(e => { console.error('DEMO FAIL', e); if (agent) agent.kill(); process.exit(1); });
