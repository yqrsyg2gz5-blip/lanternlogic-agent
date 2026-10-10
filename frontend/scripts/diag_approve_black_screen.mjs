// 复现「点允许一次 → 屏幕全黑」的排查脚本（用假响应拦下审批，不真的执行命令）。
//
// 用户报：「组长模式拆解后，他们在首页执行任务，要求点允许一次，我点完允许一次，
//          屏幕就没了，全屏纯颜色/花了/黑了」
//
// 抓四样东西：① 点之前/之后的截图 ② 页面错误与 console error
//            ③ 点之后 DOM 是否异常（body 背景、元素数量、是否有超大遮罩）
//            ④ 审批请求是否发出、界面有没有卡在某个状态
//
// 用法：cd frontend && node scripts/diag_approve_black_screen.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'approve-black');
mkdirSync(SHOTS, { recursive: true });

const H = { 'X-Auth-Token': process.env.DSH_LAN_TOKEN || '' };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers: H })).json();
const waiting = (tasks ?? []).filter((t) => t.status === 'waiting_approval');
console.log(`等审批的任务 ${waiting.length} 个：${waiting.map((t) => t.id).join(', ')}`);
if (!waiting.length) { console.log('没有等审批的任务，先跑一个需要审批的任务再试'); process.exit(1); }

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);

const errors = [];
const approves = [];
page.on('pageerror', (e) => errors.push('[pageerror] ' + String(e).slice(0, 300)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 200)); });
await page.route(/\/api\/v1\/tasks\/[^/]+\/approve(\?|$)/, async (route) => {
  approves.push(route.request().postData() ?? '');
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

// 逐个试：打开任务 → 找"允许一次"按钮
let target = null;
const items = page.locator('.taskitem');
for (const t of waiting) {
  for (let i = 0; i < Math.min(await items.count(), 25); i += 1) {
    await items.nth(i).click();
    await page.waitForTimeout(600);
    const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
    if (!sub.includes(t.id)) continue;
    const btn = page.locator('button', { hasText: /允许一次|允许/ }).first();
    if (await btn.count()) { target = { t, btn }; break; }
  }
  if (target) break;
}
if (!target) { console.log('没找到「允许一次」按钮（审批条可能没渲染）'); await browser.close(); process.exit(1); }
console.log(`用任务：${target.t.id}`);

const snap = async (tag) => {
  await page.screenshot({ path: path.join(SHOTS, `${tag}.png`) });
  return page.evaluate(() => {
    const b = getComputedStyle(document.body);
    const big = [...document.querySelectorAll('body *')].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > window.innerWidth * 0.95 && r.height > window.innerHeight * 0.95;
    }).map((el) => `${el.tagName}.${(el.className || '').toString().slice(0, 30)}`);
    return {
      bodyBg: b.backgroundColor, bodyColor: b.color,
      elements: document.querySelectorAll('body *').length,
      bodyText: (document.body.innerText || '').slice(0, 80).replace(/\n/g, ' '),
      fullscreenish: big.slice(0, 6),
    };
  });
};

const before = await snap('01-before');
console.log('点之前：', JSON.stringify(before));
await target.btn.click();
await page.waitForTimeout(2500);
const after = await snap('02-after');
console.log('点之后：', JSON.stringify(after));
console.log('审批请求：', approves.length ? approves[0].slice(0, 60) : '（没发出）');
console.log('页面错误：', errors.length ? errors.slice(0, 4).join(' | ') : '无');
console.log(`截图目录：${SHOTS}`);
await browser.close();
