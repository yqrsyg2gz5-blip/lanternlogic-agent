// A-4 验证（真浏览器）：设置页的「查看密码（仅本机）」与「改密码」
//
// 判据：
//   ① 常规节里有「查看密码（仅本机）」按钮
//   ② 点它 → 显示密码（等宽字体）+ 复制按钮
//   ③ 「改密码」展开后有输入框、随机生成、保存按钮
//   ④ 点「随机生成」→ 输入框里出现 ≥20 位、无空格的串
//   ⑤ 保存 → POST /api/v1/server/token，body 里是那个新密码
//   ⑥ 后端 403（手机场景）时**如实显示**"只能在本机查看"，而不是静默失败
//   ⑦ 无页面错误
//
// ★ 安全：所有请求 route 拦截假响应 ——不会真的改密码。
// 用法：cd frontend && node scripts/verify_access_token_ui.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });

async function open({ forbidden = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  await applyLanAuth(page);
  const errors = [];
  const posts = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  await page.route(/\/api\/v1\/server\/token(\?|$)/, async (route) => {
    const req = route.request();
    if (req.method() === 'POST') {
      posts.push(req.postData() ?? '');
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, token: JSON.parse(req.postData() || '{}').token || 'gen-token-abcdefghijklmnop',
          generated: !JSON.parse(req.postData() || '{}').token,
          note: '已修改：旧密码立即失效，手机/其它设备需要用新密码重新连接（设置页二维码也要重新扫）。' }) });
      return;
    }
    if (forbidden) {
      await route.fulfill({ status: 403, contentType: 'application/json',
        body: JSON.stringify({ detail: '访问密码只能在本机上查看（手机或其它设备看不到）——请在电脑上打开设置页' }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, token: 'VNSBQXnJJGoO7rBk2qymvwweqlfIhjcl', set: true,
        note: '这是本机局域网访问密码；手机连的时候输入的就是它。' }) });
  });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
  await fab.waitFor({ state: 'visible', timeout: 15000 });
  await fab.click();
  await page.waitForTimeout(600);
  await page.locator('button.nav-item', { hasText: '常规' }).first().click();
  await page.waitForTimeout(700);
  return { page, errors, posts };
}

// ── A. 本机：能看到、能改 ──
const A = await open();
const viewBtn = A.page.locator('button.btn-mini', { hasText: '查看密码' }).first();
check('① 常规节有「查看密码（仅本机）」', await viewBtn.count() > 0);
await viewBtn.click();
await A.page.waitForTimeout(500);
const shown = (await A.page.locator('.token-value').first().innerText().catch(() => '')).trim();
// 两边都去掉不可见字符再比（innerText 可能带零宽字符/软换行 —— 第一版就因为这个假红）
const norm = (s) => s.replace(/\s+/g, '').replace(/[\u200b-\u200f\ufeff]/g, '');
check('② 点开后显示密码', norm(shown) === 'VNSBQXnJJGoO7rBk2qymvwweqlfIhjcl',
  `${JSON.stringify(shown).slice(0, 60)}（长度 ${shown.length}）`);
check('③ 有复制按钮', await A.page.locator('button.btn-mini', { hasText: '复制' }).count() > 0);

await A.page.locator('button.btn-mini', { hasText: '改密码' }).first().click();
await A.page.waitForTimeout(300);
check('④ 展开后有输入框与随机生成', await A.page.locator('.token-input').count() > 0
  && await A.page.locator('button.btn-mini', { hasText: '随机生成' }).count() > 0);
await A.page.locator('button.btn-mini', { hasText: '随机生成' }).first().click();
await A.page.waitForTimeout(200);
const gen = await A.page.locator('.token-input').inputValue();
check('⑤ 随机生成的密码够强（≥20 位、无空格）',
  gen.length >= 20 && !/\s/.test(gen), `${gen.length} 位：${gen.slice(0, 12)}…`);

await A.page.locator('button.btn-mini', { hasText: '保存新密码' }).first().click();
await A.page.waitForTimeout(600);
check('⑥ 保存发出 POST 且 body 里是新密码',
  A.posts.length > 0 && new RegExp(`"token":\\s*"${gen}"`).test(A.posts[0]), (A.posts[0] ?? '').slice(0, 70));
const note = (await A.page.locator('.sec-note').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑦ 回执说清"旧密码立即失效"', /旧密码立即失效/.test(note), note.slice(0, 60));
check('⑧ A 组无页面错误', A.errors.length === 0, A.errors.join(' | '));

// ── B. 手机场景（后端 403）：如实显示，不静默失败 ──
const B = await open({ forbidden: true });
await B.page.locator('button.btn-mini', { hasText: '查看密码' }).first().click();
await B.page.waitForTimeout(500);
const deny = (await B.page.locator('.sec-note').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑨ 403 时如实说明"只能在本机查看"', /只能在本机/.test(deny), deny.slice(0, 70));
check('⑩ 拿不到密码时不显示任何值', await B.page.locator('.token-value').count() === 0);
check('⑪ B 组无页面错误', B.errors.length === 0, B.errors.join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
