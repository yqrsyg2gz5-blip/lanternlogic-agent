import { applyLanAuth } from './_lanauth.mjs';
// 验证：手机直连「一键开通」面板（第 8 批 问题4）
//
// 用户原话：「对小白来说他根本就不会设置——你写一个什么 backend config.json，
//            他们根本不懂、不知道搁哪找」。
// 修前：设置页只有一段 4 步手工说明，而且第 4 步教人打开 http://电脑IP:5173 ——
//       那个端口 vite 只绑 localhost，外部本来就进不来（说明本身是失效的）。
// 修后：一键开通（自动生成访问密码）+ 直接给出手机能打开的完整链接 + 重启提示。
//
// ★ 安全（施工纪律）：所有写请求（POST /lan/enable|disable）一律 page.route 拦截 +
//   假响应 —— 绝不落到后端，因此**不会改动用户真实的 config.json**。全程不用 force click。
//
// 用法：cd frontend && node scripts/verify_lan_onboarding.mjs
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:5173';
const DISABLED = {
  enabled: false, pending_lan: false, needs_restart: false, host: '127.0.0.1', port: 8642,
  ip: '192.168.0.155', token: '', token_set: false, url: 'http://192.168.0.155:8642/',
  ui_ready: true, ui_built_at: '2026-10-04T00:00:00Z',
};
// ★ 开通后但**还没重启**：真实后端返回的是 pending_lan=true 而 enabled 仍为 false
//   （监听地址是 uvicorn 启动时定下的，运行中改不了）。假数据必须照这个语义来，
//   否则测的就不是真实行为了 —— 本脚本第一版就写错成 enabled:true，被 ⑤ 号判据抓住。
const PENDING = {
  ...DISABLED, enabled: false, pending_lan: true, needs_restart: true, host: '0.0.0.0',
  token: 'FAKE-TOKEN-for-test-only-123456', token_set: true,
  url: 'http://192.168.0.155:8642/?token=FAKE-TOKEN-for-test-only-123456',
};
const LIVE = { ...PENDING, enabled: true, needs_restart: false };

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const writes = [];
let statusBody = DISABLED;
await page.route('**/api/v1/lan/**', async (route) => {
  const req = route.request();
  if (req.method() === 'POST') {
    writes.push({ url: req.url(), method: req.method() });
    statusBody = PENDING;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, ...PENDING }) });
    return;
  }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(statusBody) });
});

const openLanCard = async () => {
  const fab = page.locator('button[title="设置"]').first();
  await fab.waitFor({ state: 'visible', timeout: 15000 });
  await fab.click();
  await page.waitForTimeout(700);
  // ★ 设置面板默认停在「模型设置」，手机直连卡片在「常规」页签里
  await page.locator('button.nav-item', { hasText: '常规' }).first().click();
  await page.waitForTimeout(500);
  const c = page.locator('.card', { hasText: '手机直连' }).first();
  await c.waitFor({ state: 'visible', timeout: 8000 });
  return c;
};

// 打开设置 → 常规
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);
const card = await openLanCard();
const cardText = async () => (await card.innerText()).replace(/\s+/g, ' ');

// ① 关着的时候：一键开通 + 不再教用户改配置文件
const t1 = await cardText();
check('① 未开通时显示「仅本机」', t1.includes('仅本机'), t1.slice(0, 60));
check('② 有「一键开通」按钮', await card.locator('button', { hasText: '一键开通' }).count() > 0);
check('③ ★ 不再出现「改 config.json」这种手工说明',
  !t1.includes('config.json') && !t1.includes('server.host'), t1.slice(0, 120));

// ② 点开通 → 面板变成"等待重启"，并给出重启办法
await card.locator('button', { hasText: '一键开通' }).click();
await page.waitForTimeout(600);
const t2 = await cardText();
check('④ 点击后发出 POST /lan/enable', writes.some((w) => w.url.includes('/lan/enable')), JSON.stringify(writes));
check('⑤ 变成「等待重启生效」（而不是假装已经通了）', t2.includes('等待重启'), t2.slice(0, 80));
check('⑥ 明确告诉用户怎么重启', t2.includes('重启') && t2.includes('start.bat'), t2.slice(0, 140));

// ★ 关键回归点：开通时必须把访问密码存进本机 localStorage。
//   不存的话，重启后 token 闸门对本机也生效 ⇒ 本机浏览器被自己锁在外面，
//   而那时设置页同样 401，用户连密码都看不到（只能回去翻 config.json ——
//   正是这个功能要消灭的事）。
const stored = await page.evaluate(() => localStorage.getItem('authToken'));
check('⑥b ★ 开通时把密码存进本机（防重启后本机自锁）',
  stored === 'FAKE-TOKEN-for-test-only-123456', String(stored));

// ③ 已开通状态：给出手机能直接打开的完整链接 + 复制按钮
statusBody = LIVE;
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);
const card2 = await openLanCard();
const t3 = (await card2.innerText()).replace(/\s+/g, ' ');
check('⑦ 已开通时显示「已开通（手机可连）」', t3.includes('已开通'), t3.slice(0, 60));
check('⑧ ★ 给出带访问密码的完整链接（手机点开即登录）',
  t3.includes('192.168.0.155:8642') && t3.includes('token='), t3.slice(0, 160));
check('⑨ 有「复制链接」按钮', await card2.locator('button', { hasText: '复制链接' }).count() > 0);
check('⑩ 有安全警示（别在公共 WiFi 开）', t3.includes('公共 WiFi') || t3.includes('公共WiFi'), t3.slice(0, 200));

check('⑪ 无页面错误', errors.length === 0, errors.join(' | '));

const bad = results.filter((r) => !r.ok).length;
console.log(`\n写请求拦截计数 = ${writes.length}（全部被 page.route 拦下，0 次落盘 —— 没碰你的 config.json）`);
console.log(`结果：${results.length - bad}/${results.length} 通过`);
await browser.close();
process.exit(bad === 0 ? 0 : 1);
