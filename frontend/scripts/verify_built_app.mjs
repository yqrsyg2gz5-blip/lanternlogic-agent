// ★ 验证"用户真正用的那个应用"（后端托管的 frontend/dist，端口 8642）。
//
// 为什么单独有这个脚本：我所有的界面验证都跑在 dev server（5173），而用户吃的是 dist。
// 2026-10-05 的教训 —— dist 停在 15 小时前，用户一整天没看到任何界面修复，
// 还因此踩到早就修好的崩溃（旧包里"搜索改 DOM"的 bug + 没有错误边界 ⇒ 整页全黑）。
// 所以：**改完前端必须 npm run build，并且到这里验一遍**。
//
// 用法：cd frontend && node scripts/verify_built_app.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = process.env.DSH_APP_BASE || 'http://127.0.0.1:8642';
const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'OK ' : 'BAD'}  ${name}${extra ? '  | ' + extra : ''}`);
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 160)); });

const resp = await page.goto(BASE, { waitUntil: 'domcontentloaded' });
check('① 应用能打开', !!resp && resp.status() === 200, `HTTP ${resp?.status()}`);
await page.waitForTimeout(1500);
check('② 首页渲染出内容', (await page.locator('body').innerText()).length > 20);

// ③ 产物里带没带新功能（直接读打包后的 css/js —— 这是"用户拿到没拿到"的铁证）
const asset = await page.evaluate(() => {
  const css = [...document.querySelectorAll('link[rel=stylesheet]')].map((l) => l.href);
  const js = [...document.querySelectorAll('script[src]')].map((s) => s.src);
  return { css, js };
});
const cssText = await (await fetch(asset.css[0] ?? '')).text().catch(() => '');
const jsText = await (await fetch(asset.js[0] ?? '')).text().catch(() => '');
check('④ 新界面在产物里：群里审批卡片样式', cssText.includes('approval-card'), asset.css[0]?.split('/').pop() ?? '');
check('⑤ 新界面在产物里：错误边界面板', cssText.includes('crash-panel'));
check('⑥ 新功能在产物里：审批/开会/接力', jsText.includes('需要你批准') && jsText.includes('会议纪要') && jsText.includes('接力'));

// ⑦ 真点一遍：团队 → 群聊 → 群 → 模式下拉要有 5 项
await page.locator('button, a').filter({ hasText: /^团队$/ }).first().click();
await page.waitForTimeout(900);
const chatTab = page.locator('button, div').filter({ hasText: /^群聊$/ }).first();
if (await chatTab.count()) { await chatTab.click(); await page.waitForTimeout(700); }
const groupName = page.locator('.team-group-name').first();
if (await groupName.count()) { await groupName.click(); await page.waitForTimeout(1200); }
const modes = await page.evaluate(() => {
  const sel = [...document.querySelectorAll('select')].find((s) => [...s.options].some((o) => o.value === 'manual'));
  return sel ? [...sel.options].map((o) => o.value) : [];
});
check('⑦ 模式下拉含接力与开会（5 种模式）',
  modes.includes('relay') && modes.includes('meeting'), modes.join(' / ') || '(没读到)');
check('⑧ 全程无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));


await browser.close();
console.log(`\n结果：${results.filter((r) => r.ok).length}/${results.length} 通过（目标 ${BASE}）`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
