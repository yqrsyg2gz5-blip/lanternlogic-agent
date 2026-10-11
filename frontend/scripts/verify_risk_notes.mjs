// Phase 3 ⑧ 收尾批 验证（真浏览器）：高风险项说明
//
// 判据：
//   ① 授权目录 / 需审批动作 两项下面有说明条（.risk-note）
//   ② 说明**默认只显示一行摘要**（不展开详情）—— 不给设置页添乱
//   ③ 点 `?` 展开详情，且详情里讲了三件事：它控制什么 / 最坏后果 / 怎么退回
//   ④ 把「需审批动作」清空（**只在输入框里改，不保存**）⇒ 立刻变 danger 并提示"所有命令都不再问你"
//   ⑤ 授权目录填 `C:\`（同样不保存）⇒ 变 danger
//   ⑥ ★ **点说明、改输入框期间，一个写请求都不许发**（POST/PUT/PATCH/DELETE 计数必须为 0）
//      —— 这是"纯展示、零行为变更"的动态证据
//   ⑦ 无页面错误
//
// 用法：cd frontend && node scripts/verify_risk_notes.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);
const errors = [];
const writes = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 120)); });
page.on('request', (r) => {
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(r.method()) && r.url().includes('/api/v1/')) {
    writes.push(`${r.method()} ${r.url().replace(/^.*\/api\/v1/, '')}`);
  }
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1000);
const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
await fab.waitFor({ state: 'visible', timeout: 15000 });
await fab.click();
await page.waitForTimeout(700);
await page.locator('button.nav-item', { hasText: '执行环境' }).first().click();
await page.waitForTimeout(900);

const notes = page.locator('.risk-note');
check('① 两项下面都有说明条', await notes.count() >= 2, `${await notes.count()} 条`);
check('② 默认只显示一行摘要（详情不展开）', await page.locator('.risk-detail').count() === 0);

// ③ 展开
await page.locator('.risk-toggle').first().click();
await page.waitForTimeout(300);
const detail = (await page.locator('.risk-detail').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('③ 点 ? 能展开详情', detail.length > 40, detail.slice(0, 60));
check('③b 详情讲清了"控制什么/后果/怎么退回"',
  /它控制什么/.test(detail) && /(后果|会怎样)/.test(detail) && /怎么退回/.test(detail), detail.slice(0, 90));

// ④ 审批清单清空（只改输入框，不保存）
const approval = page.locator('input').filter({ hasNot: page.locator('[type=file]') });
const approvalBox = page.locator('label.fld', { hasText: '需审批动作' }).locator('input').first();
await approvalBox.fill('');
await page.waitForTimeout(400);
const danger1 = (await notes.nth(1).innerText().catch(() => '')).replace(/\s+/g, ' ');
check('④ 清空审批清单 ⇒ 立刻提示"所有命令都不再问你"',
  /risk-danger/.test(await notes.nth(1).getAttribute('class').catch(() => '')) && /都不会再问你/.test(danger1),
  danger1.slice(0, 70));

// ⑤ 授权目录填 C:\ （只改输入框，不保存）
const dirsBox = page.locator('label.fld', { hasText: '授权目录' }).locator('textarea').first();
await dirsBox.fill('C:\\');
await page.waitForTimeout(400);
const danger2 = (await notes.first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑤ 授权目录填 C:\\ ⇒ 变危险并说清"可读写、可删除"',
  /risk-danger/.test(await notes.first().getAttribute('class').catch(() => '')) && /删除|读写/.test(danger2),
  danger2.slice(0, 70));

check('⑥ ★ 全程零写请求（点说明/改输入框不许碰配置）', writes.length === 0,
  writes.length ? writes.slice(0, 3).join(' | ') : '0 个 POST/PUT/PATCH/DELETE');
check('⑦ 无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
