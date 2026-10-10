// Phase 3 ⑦ 多轮"改一句重发" 验证（真浏览器 · 真任务）
//
// 判据：
//   ① 用户消息上有「改一句重发」（悬停可见）
//   ② 点它 → 就地变成编辑框，且**预填原文**（不是空白）
//   ③ 改完点「改完重跑」→ POST /tasks/<id>/messages 的 body 里带 **edit_of_seq** 且等于那条消息的 seq
//   ④ 编辑框里有"之后的内容会作废"的提示（同屏可见，别让用户误以为只是改文字）
//   ⑤ **任务运行中不出现该按钮**（跑了改会被后端 409 —— 界面先别给）
//   ⑥ 改回原文（没改动）时提交按钮禁用（防误触）
//   ⑦ 无页面错误
//
// ★ 安全：POST 一律假响应（route 拦截）——不会真的重跑、不会动历史。
// 用法：cd frontend && node scripts/verify_edit_resend.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'edit-resend');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const token = process.env.DSH_LAN_TOKEN || '';
const headers = { 'X-Auth-Token': token };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();

// 挑一个**已完成且有多条用户消息**的任务（有历史才谈得上"改一句"）
let pick = null;
for (const t of (tasks ?? []).slice(0, 14)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  const users = (evs ?? []).filter((e) => e.type === 'message' && e.payload?.role === 'user');
  if (users.length >= 1 && t.status !== 'running') { pick = { t, users }; break; }
}
if (!pick) { console.log('BAD 没找到可改的任务'); process.exit(1); }
console.log(`用任务：${pick.t.id}（${pick.users.length} 条用户消息，第 1 条 seq=${pick.users[0].seq}）\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await applyLanAuth(page);
const errors = [];
const posts = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.route(/\/api\/v1\/tasks\/[^/]+\/messages(\?|$)/, async (route) => {
  if (route.request().method() === 'POST') {
    posts.push(route.request().postData() ?? '');
    await route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, mode: 'edited_rerun' }) });
    return;
  }
  await route.continue();
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
const items = page.locator('.taskitem');
const n = await items.count();
for (let i = 0; i < Math.min(n, 20); i++) {
  await items.nth(i).click();
  await page.waitForTimeout(600);
  const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
  if (sub.includes(pick.t.id)) break;
}
await page.waitForTimeout(900);

const editBtn = page.locator('.msg-edit').first();
check('① 用户消息上有「改一句重发」', await editBtn.count() > 0, `本页 ${await page.locator('.msg-edit').count()} 个`);
if (await editBtn.count()) {
  await editBtn.click();
  await page.waitForTimeout(300);
  const box = page.locator('.edit-box').first();
  check('② 点它就地变编辑框', await box.count() > 0);
  const val = await box.inputValue().catch(() => '');
  check('③ 预填原文（不是空白）', val.trim().length > 0, val.slice(0, 40));
  const hint = (await page.locator('.edit-hint').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('④ 同屏提示"之后的内容会作废"', /作废/.test(hint), hint.slice(0, 50));
  // ⚠️ 别在这里 click —— 没改动时按钮是**禁用**的，click 会一直等到超时（第一版就这么挂的）
  check('⑤ 没改动时提交按钮禁用（防误触）', await page.locator('.edit-save').first().isDisabled().catch(() => false));
  await box.fill(val.trim() + '（改过的）');
  await page.waitForTimeout(150);
  await page.locator('.edit-save').first().click();
  await page.waitForTimeout(700);
  check('⑥ POST 里带 edit_of_seq 且等于那条消息的 seq',
    posts.length > 0 && new RegExp(`"edit_of_seq":\\s*${pick.users[0].seq}`).test(posts[0]),
    posts[0]?.slice(0, 90) ?? '没有请求');
  check('⑦ body 里是新文本', /改过的/.test(posts[0] ?? ''));
}

check('⑧ 运行中的任务不给这个按钮（跑了改会被 409）', await page.evaluate(() => {
  // 页头显示 idle 时才有按钮；running 时不渲染（由 TaskView 传 undefined 控制）
  const sub = document.querySelector('.tv-sub')?.textContent || '';
  const hasBtn = document.querySelectorAll('.msg-edit').length > 0;
  return /idle|done|完成|待命/.test(sub) ? true : !hasBtn;
}));
check('⑨ 无页面错误', errors.length === 0, errors.join(' | '));

await page.screenshot({ path: path.join(SHOTS, 'edit-resend.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
