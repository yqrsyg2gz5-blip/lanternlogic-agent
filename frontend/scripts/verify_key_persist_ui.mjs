// Phase 1 ① 后半截验证（真浏览器）：设置页的「同时写入用户级环境变量」勾选框
// 判据：
//   ① 勾选框在位、**默认勾上**（不勾就白填：Key 只活在进程里，重启即失效）
//   ② 保存时 POST /api/v1/settings/model 的请求体里带 `persist: true`
//   ③ 取消勾选后保存 → 请求体 `persist: false`（用户能拒绝写注册表）
//   ④ 后端的回执文案要显示出来（写没写进用户级环境变量，用户必须看得见）
// ★ 安全：所有写请求 page.route 拦截 + 假响应，**绝不落到后端**；不用 force click。
// ★ URL 用正则：本应用的密码拼在 query 上，Playwright 的 glob 是全串匹配。
// 用法：cd frontend && node scripts/verify_key_persist_ui.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));

const writes = [];
let nextNote = '已写入用户级环境变量 XIAOMI_MIMO_API_KEY（51 字符）——重启后端后长期有效';
await page.route(/\/api\/v1\/settings(\?|$)/, async (route) => {
  const req = route.request();
  if (req.method() !== 'GET') {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    return;
  }
  await route.continue();
});
await page.route(/\/api\/v1\/settings\/model(\?|$)/, async (route) => {
  const req = route.request();
  if (req.method() === 'POST') {
    writes.push(req.postData() ?? '');
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, persisted: true, key_env: 'XIAOMI_MIMO_API_KEY', note: nextNote }),
    });
    return;
  }
  await route.continue();
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(600);
// 用与 verify_model_unify.mjs 同款的入口选择器（那是仓库里已验证过的写法）
const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
await fab.waitFor({ state: 'visible', timeout: 15000 });
await fab.click();
await page.waitForTimeout(600);
await page.locator('button.nav-item', { hasText: '模型设置' }).first().click();
await page.waitForTimeout(500);

const box = page.locator('input[type="checkbox"]').first();
const hasBox = await box.count();
check('① 设置页有「写入用户级环境变量」勾选框', hasBox > 0, `checkbox=${hasBox}`);
check('② 默认勾上（不勾的话 Key 重启就没）', hasBox > 0 && (await box.isChecked()));
const labelText = (await page.locator('.fld-check').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('③ 勾选框自带说明（不是个无标签的框）',
  /用户级环境变量/.test(labelText) && /重启/.test(labelText), labelText.slice(0, 90));

// 保存（勾着）→ 请求体必须带 persist:true
await page.locator('input[type="password"]').first().fill('sk-ui-probe-1234567890');
await page.locator('button.btn-save', { hasText: '保存模型配置' }).first().click();
await page.waitForTimeout(600);
check('④ 保存请求带 persist:true', writes.length > 0 && /"persist":true/.test(writes[0]),
  writes[0]?.slice(0, 120) ?? '没有写请求');
check('⑤ 后端回执文案显示出来了（写没写进用户级环境变量看得见）',
  (await page.locator('.card-hint', { hasText: '用户级环境变量' }).count()) > 0);

// 取消勾选 → persist:false
nextNote = '配置已保存。Key 已写入当前进程；要永久保存请在系统环境变量中设置。';
await box.uncheck();
await page.locator('input[type="password"]').first().fill('sk-ui-probe-2234567890');
await page.locator('button.btn-save', { hasText: '保存模型配置' }).first().click();
await page.waitForTimeout(600);
check('⑥ 取消勾选后保存带 persist:false（用户能拒绝写注册表）',
  writes.length > 1 && /"persist":false/.test(writes[1]), writes[1]?.slice(0, 120) ?? '没有第二次写请求');
check('⑦ 无页面错误', errors.length === 0, errors.join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`（写请求全部被 page.route 拦截，0 次落盘：${writes.length} 次假响应）`);
process.exit(bad === 0 ? 0 : 1);
