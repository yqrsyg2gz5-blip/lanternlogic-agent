import { applyLanAuth } from './_lanauth.mjs';
// 验证：手机直连二维码（第 8b 批）
//
// 验证思路（关键）：**不拿自己的实现验自己**。
//   · 编码：应用里用 `qrcode` 库渲染成 <img>（PNG data URL）
//   · 解码：用**独立的另一个库** `jsqr` 把那 PNG 解回文本
//   · 断言：解出来的文本 === 我们期望的完整链接（含 32 位访问密码）
//   · 再加一条"解码器不是橡皮图章"的对照：换一个链接编码，解出来必须是那个新链接
//     （否则"解出来一样"可能只是因为解码器压根没读图）
//
// 为什么必须真解码：二维码是给【手机相机】看的。只断言"画了个 canvas/img"证明不了
// 手机扫得出来 —— 静区少了、纠错等级太低、尺寸太小都会导致扫不出。
//
// ★ 安全：写请求照样全程 page.route 拦截，不碰真实 config.json。
// 用法：cd frontend && node scripts/verify_lan_qr.mjs
import { chromium } from 'playwright-core';
import jsQR from 'jsqr';
import { PNG } from 'pngjs';
import QRCode from 'qrcode';

const BASE = 'http://127.0.0.1:5173';
const TOKEN = 'QR-TEST-token-abcdefghijklmnop012345';
const URL_UNDER_TEST = `http://192.168.0.155:8642/?token=${TOKEN}`;
const CONTROL_URL = 'http://10.0.0.9:9999/?token=CONTROL-control-control';

const LIVE = {
  enabled: true, pending_lan: false, needs_restart: false, host: '0.0.0.0', port: 8642,
  ip: '192.168.0.155', token: TOKEN, token_set: true, url: URL_UNDER_TEST,
  ui_ready: true, ui_built_at: '2026-10-04T00:00:00Z',
};

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

/** PNG data URL → 用独立解码库 jsqr 解回文本 */
const decodePng = (dataUrl) => {
  const b64 = String(dataUrl).replace(/^data:image\/png;base64,/, '');
  const png = PNG.sync.read(Buffer.from(b64, 'base64'));
  const r = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
  return { text: r ? r.data : null, w: png.width, h: png.height };
};

// ── 对照组：证明解码器真的在读图（不是橡皮图章）──
const controlPng = `data:image/png;base64,${(await QRCode.toBuffer(CONTROL_URL, { margin: 1, width: 336 })).toString('base64')}`;
const control = decodePng(controlPng);
check('① 解码器有效性对照（换一个链接必须解出新链接）',
  control.text === CONTROL_URL, `解出：${String(control.text).slice(0, 50)}`);

// ── 真实路径：从界面里取二维码 ──
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.route('**/api/v1/lan/**', async (route) => {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(LIVE) });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);
await page.locator('button[title="设置"]').first().click();
await page.waitForTimeout(700);
await page.locator('button.nav-item', { hasText: '常规' }).first().click();
await page.waitForTimeout(500);

const img = page.locator('img[alt="手机扫码直连二维码"]');
check('② 已开通时界面出现二维码', await img.count() > 0, `找到 ${await img.count()} 个`);
if (await img.count() === 0) {
  await browser.close();
  console.log('\n结果：0/2 通过（没找到二维码，后续判据无法进行）');
  process.exit(1);
}
const src = await img.first().getAttribute('src');
check('③ 二维码是内嵌 PNG（不依赖外网服务，隐私安全）',
  String(src).startsWith('data:image/png;base64,'), String(src).slice(0, 40));

const got = decodePng(src);
check('④ ★ 独立解码库解出的文本 === 手机该打开的完整链接',
  got.text === URL_UNDER_TEST, `解出：${String(got.text).slice(0, 70)}`);
check('⑤ 链接里确实带着访问密码（扫完不用手打）',
  String(got.text).includes(`token=${TOKEN}`), String(got.text));
check('⑥ 二维码尺寸够手机扫（≥200px 位图）', got.w >= 200 && got.h >= 200, `${got.w}×${got.h}`);
check('⑪ 无页面错误', errors.length === 0, errors.join(' | '));

// ── ★ 第 8c 处：扫进来「能不能直接进」──
// 只验"二维码编码正确"是不够的：链接里带着密码，而本前端此前**只认 localStorage、
// 不读 URL 里的 token** ⇒ 手机会弹出密码框、要你打那 32 位随机串，二维码等于白做。
// 这里验：打开带 ?token= 的链接后 ① 密码被收进 localStorage ② 地址栏里的密码被抹掉。
const CARRY = 'CARRY-token-through-url-0123456789';
await page.goto(`${BASE}/?token=${CARRY}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
const carried = await page.evaluate(() => localStorage.getItem('authToken'));
check('⑫ ★ 链接里的密码被收进本机（否则扫码进来还要手打 32 位）', carried === CARRY, String(carried));
const urlNow = page.url();
check('⑬ 密码已从地址栏抹掉（不留历史/截图/Referer）',
  !urlNow.includes(CARRY) && !urlNow.includes('token='), urlNow);

const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
await browser.close();
process.exit(bad === 0 ? 0 : 1);
